"""OpenAI-driven agent loop, gated by the same hook engine as Claude.

This is the second LLM driver. Same `HookEngine`, same `Tool` surface,
same `RunState` — only the wire format and the SDK differ. The point is
that the substrate is **provider-agnostic**: the hook chain, the
credential proxy, the policy gate, and the audit log are unchanged
regardless of which model is in the seat.

This loop is hand-rolled (not the SDK's automatic tool-runner) so each
tool call passes through PreToolUse / PostToolUse hooks individually.
That's the seam that makes the safety story real.
"""

from __future__ import annotations

import json
import time
from typing import Any

from ..core.hooks import (
    HookEngine,
    PostToolUseEvent,
    PreToolUseEvent,
    TaskCompleteEvent,
    ToolBlocked,
)
from ..core.state import RunState, StepRecord, ToolCall
from ..tools.base import Tool

DEFAULT_MODEL = "gpt-4o-mini"  # cheapest sensible default
DEFAULT_MAX_STEPS = 12

DEFAULT_SYSTEM_PROMPT = """You are a careful analysis agent.

You have a small set of tools available. For each tool, the platform's
hook engine, policy, and credential proxy gate every call you make —
so you only need to worry about what to do, not how to keep it safe.

Plan briefly, then execute. Use only the tools provided; do not invent
new ones. When the task is complete, summarize the result in plain
prose and stop calling tools.

If a tool call comes back with an error or "BLOCKED" message, that is
the platform refusing the action. Adapt your plan or stop — do not
retry the same call expecting a different outcome.
"""


class OpenAIAgentRunner:
    """Hook-driven OpenAI tool-use loop.

    Public API mirrors :class:`claude_demo.agents.claude.AgentRunner` so
    the two are interchangeable from the caller's perspective.
    """

    def __init__(
        self,
        tools: list[Tool],
        hooks: HookEngine,
        client: Any = None,
        model: str = DEFAULT_MODEL,
        max_steps: int = DEFAULT_MAX_STEPS,
        system_prompt: str = DEFAULT_SYSTEM_PROMPT,
    ) -> None:
        if client is None:
            # Lazy import — the rest of the package has no hard dependency
            # on openai, and the keyless headline demo doesn't need it.
            from openai import OpenAI  # type: ignore

            client = OpenAI()

        self.client = client
        self.hooks = hooks
        self.model = model
        self.max_steps = max_steps
        self.system_prompt = system_prompt

        self.tools = {t.name: t for t in tools}
        # Convert platform Tool schemas to OpenAI's "function tool" shape.
        self.tool_schemas = [
            {
                "type": "function",
                "function": {
                    "name": t.name,
                    "description": t.description,
                    "parameters": t.input_schema,
                },
            }
            for t in tools
        ]

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------

    def run(self, user_prompt: str) -> RunState:
        state = RunState(user_input=user_prompt)
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": self.system_prompt},
            {"role": "user", "content": user_prompt},
        ]
        final_text = ""

        for step_index in range(1, self.max_steps + 1):
            response = self.client.chat.completions.create(
                model=self.model,
                tools=self.tool_schemas,
                messages=messages,
            )
            choice = response.choices[0]
            message = choice.message
            text = message.content or ""
            tool_calls = list(message.tool_calls or [])

            usage = getattr(response, "usage", None)
            step = StepRecord(
                step=step_index,
                stop_reason=choice.finish_reason or "",
                text=text,
                input_tokens=getattr(usage, "prompt_tokens", 0) or 0,
                output_tokens=getattr(usage, "completion_tokens", 0) or 0,
            )

            # Echo the assistant turn back into history. OpenAI requires
            # the tool_calls to round-trip verbatim so it can match the
            # tool messages we append next.
            assistant_entry: dict[str, Any] = {
                "role": "assistant",
                "content": text or None,
            }
            if tool_calls:
                assistant_entry["tool_calls"] = [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {
                            "name": tc.function.name,
                            "arguments": tc.function.arguments,
                        },
                    }
                    for tc in tool_calls
                ]
            messages.append(assistant_entry)

            if not tool_calls:
                # No more tool calls — model is done.
                final_text = text.strip()
                state.add_step(step)
                break

            for tc in tool_calls:
                try:
                    tool_input = json.loads(tc.function.arguments or "{}")
                except json.JSONDecodeError:
                    tool_input = {"_raw": tc.function.arguments or ""}

                call_record, tool_message = self._dispatch_tool(
                    step_index,
                    tool_name=tc.function.name,
                    tool_input=tool_input,
                    tool_call_id=tc.id,
                )
                step.tool_calls.append(call_record)
                messages.append(tool_message)

            state.add_step(step)
        else:
            # Hit max_steps without a final answer.
            if not final_text:
                final_text = "(agent stopped: max_steps reached without a final answer)"

        state.finish(final_text)

        self.hooks.fire_complete(
            TaskCompleteEvent(
                final_text=final_text,
                step_count=state.step_count,
                tool_call_count=state.tool_call_count,
                metadata={
                    "input_tokens": state.total_input_tokens,
                    "output_tokens": state.total_output_tokens,
                    "provider": "openai",
                    "model": self.model,
                },
            )
        )
        return state

    # ------------------------------------------------------------------
    # Tool dispatch — the seam where hooks intercept each call
    # ------------------------------------------------------------------

    def _dispatch_tool(
        self,
        step: int,
        *,
        tool_name: str,
        tool_input: dict[str, Any],
        tool_call_id: str,
    ) -> tuple[ToolCall, dict[str, Any]]:
        """Run one tool call through pre-hook → tool → post-hook.

        Returns (RunState record, OpenAI tool message). The tool message
        is the ``role: "tool"`` entry the next API request needs.
        """
        pre_event = PreToolUseEvent(tool_name=tool_name, tool_input=tool_input)

        try:
            self.hooks.fire_pre(pre_event)
        except ToolBlocked as exc:
            pre_event.blocked = True
            pre_event.block_reason = exc.reason

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
                    "role": "tool",
                    "tool_call_id": tool_call_id,
                    "content": blocked_msg,
                },
            )

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
                tool_result_text = (
                    f"ERROR: tool raised {type(exc).__name__}: {exc}"
                )
                is_error = True

        duration_ms = int((time.monotonic() - started) * 1000)

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
                "role": "tool",
                "tool_call_id": tool_call_id,
                "content": post_event.tool_result,
            },
        )
