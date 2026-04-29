"""Key-free scripted execution path.

Same hook engine, same tools, same RunState — but the "planner" is a
hand-written list of tool calls instead of an LLM. Use this when:

  - you want to demo the runtime without an Anthropic / OpenAI key
  - you want a deterministic trajectory for tests, CI, or replays
  - you want the runtime as a library and your *own* code decides what to call

The Claude-driven AgentRunner in agent.py is unchanged; this is a parallel
runtime that exercises every other piece of the system (hooks, sandbox,
permissions, state, workflow).
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

from .hooks import (
    HookEngine,
    PostToolUseEvent,
    PreToolUseEvent,
    TaskCompleteEvent,
    ToolBlocked,
)
from .state import RunState, StepRecord, ToolCall
from .tools import Tool


@dataclass
class PlannedCall:
    """One scripted tool invocation."""

    tool_name: str
    tool_input: dict[str, Any]
    # Free-text rationale that shows up in the run report — keeps demo output
    # readable without needing an LLM to narrate.
    rationale: str = ""


@dataclass
class ScriptedPlan:
    """An ordered list of PlannedCall plus a final-text template."""

    calls: list[PlannedCall] = field(default_factory=list)
    final_text: str = ""

    def add(self, tool_name: str, tool_input: dict[str, Any], rationale: str = "") -> "ScriptedPlan":
        self.calls.append(PlannedCall(tool_name=tool_name, tool_input=tool_input, rationale=rationale))
        return self


class ScriptedRunner:
    """Run a fixed plan through the hook engine.

    Mirrors AgentRunner._dispatch_tool: pre-hooks (with block / rewrite),
    tool execution, post-hooks (with transform), append to RunState. Fires
    TaskComplete once at the end.
    """

    def __init__(self, tools: list[Tool], hooks: HookEngine) -> None:
        self.tools = {t.name: t for t in tools}
        self.hooks = hooks

    def run(self, user_prompt: str, plan: ScriptedPlan) -> RunState:
        state = RunState(user_input=user_prompt)

        for index, call in enumerate(plan.calls, start=1):
            step = StepRecord(step=index, stop_reason="tool_use", text=call.rationale)
            tool_call = self._dispatch(index, call)
            step.tool_calls.append(tool_call)
            state.add_step(step)

        final_text = plan.final_text or "(scripted run complete)"
        state.finish(final_text)

        self.hooks.fire_complete(
            TaskCompleteEvent(
                final_text=final_text,
                step_count=state.step_count,
                tool_call_count=state.tool_call_count,
            )
        )
        return state

    def _dispatch(self, step: int, call: PlannedCall) -> ToolCall:
        pre = PreToolUseEvent(tool_name=call.tool_name, tool_input=dict(call.tool_input))
        try:
            self.hooks.fire_pre(pre)
        except ToolBlocked as exc:
            pre.blocked = True
            pre.block_reason = exc.reason

        if pre.blocked:
            blocked_msg = f"BLOCKED by hook: {pre.block_reason}"
            return ToolCall(
                step=step,
                tool_name=call.tool_name,
                tool_input=pre.tool_input,
                result=blocked_msg,
                is_error=True,
                blocked=True,
                block_reason=pre.block_reason,
            )

        tool = self.tools.get(call.tool_name)
        started = time.monotonic()
        if tool is None:
            return ToolCall(
                step=step,
                tool_name=call.tool_name,
                tool_input=pre.tool_input,
                result=f"ERROR: unknown tool '{call.tool_name}'",
                is_error=True,
                duration_ms=int((time.monotonic() - started) * 1000),
            )

        try:
            result_text = tool.run(pre.tool_input)
            is_error = result_text.startswith("ERROR:")
        except Exception as exc:  # noqa: BLE001 - tool boundary
            result_text = f"ERROR: tool raised {type(exc).__name__}: {exc}"
            is_error = True

        duration_ms = int((time.monotonic() - started) * 1000)

        post = PostToolUseEvent(
            tool_name=call.tool_name,
            tool_input=pre.tool_input,
            tool_result=result_text,
            is_error=is_error,
        )
        self.hooks.fire_post(post)

        return ToolCall(
            step=step,
            tool_name=call.tool_name,
            tool_input=pre.tool_input,
            result=post.tool_result,
            is_error=post.is_error,
            duration_ms=duration_ms,
        )
