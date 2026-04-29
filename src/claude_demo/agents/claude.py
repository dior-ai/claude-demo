"""Claude-driven agent loop, gated by hooks.

This is the runtime heart of the demo. A user prompt enters; Claude plans
and selects tools; the hook engine intercepts every tool call (pre + post);
the sandbox executes the body; results flow back to the model; loop until
end_turn or max_steps.

The loop is manual rather than the SDK's tool runner — we need to intercept
each tool call individually so PreToolUse hooks can block / rewrite, and
PostToolUse hooks can transform results. The SDK's auto-runner doesn't
expose those seams.

The structure mirrors the documented agentic loop:
  while True:
      response = client.messages.create(messages=...)
      if stop_reason == "end_turn": break
      append assistant content
      run each tool_use through hooks + dispatch
      append tool_results as a single user turn
"""

from __future__ import annotations

import time
from typing import Any

# `anthropic` is imported lazily inside __init__ so that the rest of the
# package — and the headline scripted demo — has no hard dependency on
# the Anthropic SDK or an API key.

from ..core.hooks import (
    HookEngine,
    PostToolUseEvent,
    PreToolUseEvent,
    TaskCompleteEvent,
    ToolBlocked,
)
from ..core.state import RunState, StepRecord, ToolCall
from ..tools.base import Tool

DEFAULT_MODEL = "claude-opus-4-7"
DEFAULT_MAX_TOKENS = 16_000
DEFAULT_MAX_STEPS = 12

DEFAULT_SYSTEM_PROMPT = """You are a careful analysis agent.

You have two tools:
  - file_tool: list / stat / head / read files in the input directory
  - code_runner: execute Python in a secure sandbox (no network, no shell)

Plan briefly, then execute. Use file_tool for inspection, code_runner for
real work. Keep code blocks small and self-contained. Print everything you
need with print() — that's the only way to surface output.

When the task is complete, summarize the result in plain prose. Do not call
more tools after you've answered.
"""


class AgentRunner:
    """Hook-driven Claude agent loop.

    Construct with the tools, hook engine, and (optionally) a system prompt.
    Call .run(user_prompt) to execute one task end-to-end and get a RunState.
    """

    def __init__(
        self,
        tools: list[Tool],
        hooks: HookEngine,
        client: Any = None,
        model: str = DEFAULT_MODEL,
        max_tokens: int = DEFAULT_MAX_TOKENS,
        max_steps: int = DEFAULT_MAX_STEPS,
        system_prompt: str = DEFAULT_SYSTEM_PROMPT,
    ) -> None:
        if client is None:
            # Lazy import — keeps the rest of the package keyless.
            from anthropic import Anthropic  # type: ignore

            client = Anthropic()
        self.tools = {t.name: t for t in tools}
        self.tool_schemas = [t.schema() for t in tools]
        self.hooks = hooks
        self.client = client
        self.model = model
        self.max_tokens = max_tokens
        self.max_steps = max_steps
        self.system_prompt = system_prompt

    def run(self, user_prompt: str) -> RunState:
        state = RunState(user_input=user_prompt)
        messages: list[dict[str, Any]] = [{"role": "user", "content": user_prompt}]
        final_text = ""

        for step_index in range(1, self.max_steps + 1):
            response = self.client.messages.create(
                model=self.model,
                max_tokens=self.max_tokens,
                system=self.system_prompt,
                tools=self.tool_schemas,
                messages=messages,
            )

            # Extract text + tool_use blocks from the response.
            text_chunks: list[str] = []
            tool_uses: list[Any] = []
            for block in response.content:
                btype = getattr(block, "type", None)
                if btype == "text":
                    text_chunks.append(block.text)
                elif btype == "tool_use":
                    tool_uses.append(block)

            step = StepRecord(
                step=step_index,
                stop_reason=response.stop_reason or "",
                text="\n".join(text_chunks),
                input_tokens=getattr(response.usage, "input_tokens", 0) or 0,
                output_tokens=getattr(response.usage, "output_tokens", 0) or 0,
            )

            # Always echo the assistant turn back into history before we
            # process tool calls — Claude needs to see its own tool_use block
            # paired with our tool_result on the next turn.
            messages.append({"role": "assistant", "content": response.content})

            if response.stop_reason == "end_turn":
                final_text = "\n".join(text_chunks).strip()
                state.add_step(step)
                break

            if not tool_uses:
                # Nothing to dispatch but model didn't end the turn — bail
                # rather than spin. This shouldn't normally happen.
                final_text = "\n".join(text_chunks).strip()
                state.add_step(step)
                break

            # Dispatch each tool_use through the hook engine.
            tool_results_content: list[dict[str, Any]] = []
            for tu in tool_uses:
                call_record, result_block = self._dispatch_tool(
                    step_index, tu.name, dict(tu.input), tu.id
                )
                step.tool_calls.append(call_record)
                tool_results_content.append(result_block)

            # Tool results are returned to the model in one user message.
            messages.append({"role": "user", "content": tool_results_content})
            state.add_step(step)

        else:
            # Loop exited via max_steps without an end_turn. Synthesize a note.
            final_text = (
                final_text
                or "(agent stopped: reached max_steps without producing a final answer)"
            )

        state.finish(final_text)

        self.hooks.fire_complete(
            TaskCompleteEvent(
                final_text=final_text,
                step_count=state.step_count,
                tool_call_count=state.tool_call_count,
                metadata={
                    "input_tokens": state.total_input_tokens,
                    "output_tokens": state.total_output_tokens,
                },
            )
        )
        return state

    def _dispatch_tool(
        self,
        step: int,
        tool_name: str,
        tool_input: dict[str, Any],
        tool_use_id: str,
    ) -> tuple[ToolCall, dict[str, Any]]:
        """Run one tool through pre-hook -> tool -> post-hook.

        Returns the ToolCall record (for state) and the tool_result content
        block (for the next API request).
        """
        pre_event = PreToolUseEvent(tool_name=tool_name, tool_input=tool_input)

        # Fire pre-hooks. They may block, or rewrite tool_input in place.
        try:
            self.hooks.fire_pre(pre_event)
        except ToolBlocked as e:
            # Some PreHook types raise instead of setting blocked=True.
            pre_event.blocked = True
            pre_event.block_reason = e.reason

        if pre_event.blocked:
            blocked_msg = f"BLOCKED by hook: {pre_event.block_reason}"
            return (
                ToolCall(
                    step=step,
                    tool_name=tool_name,
                    tool_input=tool_input,
                    result=blocked_msg,
                    is_error=True,
                    blocked=True,
                    block_reason=pre_event.block_reason,
                ),
                {
                    "type": "tool_result",
                    "tool_use_id": tool_use_id,
                    "content": blocked_msg,
                    "is_error": True,
                },
            )

        # Execute. Catch exceptions so a buggy tool doesn't kill the run —
        # surface the error to the model so it can adapt.
        tool = self.tools.get(tool_name)
        started = time.monotonic()
        if tool is None:
            tool_result_text = f"ERROR: unknown tool '{tool_name}'"
            is_error = True
        else:
            try:
                tool_result_text = tool.run(pre_event.tool_input)
                is_error = tool_result_text.startswith("ERROR:")
            except Exception as exc:  # noqa: BLE001 - tool boundary
                tool_result_text = f"ERROR: tool raised {type(exc).__name__}: {exc}"
                is_error = True
        duration_ms = int((time.monotonic() - started) * 1000)

        # Post-hooks may transform tool_result_text in place.
        post_event = PostToolUseEvent(
            tool_name=tool_name,
            tool_input=pre_event.tool_input,
            tool_result=tool_result_text,
            is_error=is_error,
        )
        self.hooks.fire_post(post_event)

        return (
            ToolCall(
                step=step,
                tool_name=tool_name,
                tool_input=pre_event.tool_input,
                result=post_event.tool_result,
                is_error=post_event.is_error,
                duration_ms=duration_ms,
            ),
            {
                "type": "tool_result",
                "tool_use_id": tool_use_id,
                "content": post_event.tool_result,
                "is_error": post_event.is_error,
            },
        )
