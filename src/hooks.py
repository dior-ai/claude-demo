"""Lifecycle hooks for the agent runner.

Three hook points fire during the agent loop:

  PreToolUse   - before a tool executes. Can BLOCK (raises ToolBlocked) or
                 redact the input before it reaches the tool.
  PostToolUse  - after a tool returns. Can transform / truncate the result
                 before it goes back to the LLM.
  TaskComplete - once Claude returns end_turn. Final summary hook.

Hooks are registered with HookEngine.add(). They are kept as simple
modules — drop in a new hook by writing a function and calling .add().
This is what makes the system feel real: the hooks actually modify
behavior, not just log.

Inspired by the claude-code-hooks repo (PreToolUse / PostToolUse / Stop
event taxonomy), but adapted to a one-process Python runtime.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable


class ToolBlocked(Exception):
    """Raised by a PreToolUse hook to refuse a tool call.

    The agent loop catches this and feeds the reason back to the LLM as a
    tool error so the model can re-plan instead of crashing the run.
    """

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


@dataclass
class PreToolUseEvent:
    tool_name: str
    tool_input: dict[str, Any]
    # Mutable: a hook can rewrite tool_input before the tool runs.
    # Set blocked=True (or raise ToolBlocked) to refuse the call entirely.
    blocked: bool = False
    block_reason: str = ""


@dataclass
class PostToolUseEvent:
    tool_name: str
    tool_input: dict[str, Any]
    tool_result: str
    is_error: bool = False
    # Mutable: a hook can rewrite tool_result before it goes back to the LLM.


@dataclass
class TaskCompleteEvent:
    final_text: str
    step_count: int
    tool_call_count: int
    metadata: dict[str, Any] = field(default_factory=dict)


PreHook = Callable[[PreToolUseEvent], None]
PostHook = Callable[[PostToolUseEvent], None]
CompleteHook = Callable[[TaskCompleteEvent], None]


class HookEngine:
    """Registry + dispatcher for lifecycle hooks.

    Hooks fire in registration order. A pre-hook can block the call; once
    blocked, no further pre-hooks run (fail-fast).
    """

    def __init__(self) -> None:
        self._pre: list[PreHook] = []
        self._post: list[PostHook] = []
        self._complete: list[CompleteHook] = []

    def add_pre(self, fn: PreHook) -> "HookEngine":
        self._pre.append(fn)
        return self

    def add_post(self, fn: PostHook) -> "HookEngine":
        self._post.append(fn)
        return self

    def add_complete(self, fn: CompleteHook) -> "HookEngine":
        self._complete.append(fn)
        return self

    def fire_pre(self, event: PreToolUseEvent) -> None:
        for hook in self._pre:
            try:
                hook(event)
            except ToolBlocked as e:
                event.blocked = True
                event.block_reason = e.reason
                return
            if event.blocked:
                return

    def fire_post(self, event: PostToolUseEvent) -> None:
        for hook in self._post:
            hook(event)

    def fire_complete(self, event: TaskCompleteEvent) -> None:
        for hook in self._complete:
            hook(event)


# ---------------------------------------------------------------------------
# Built-in hooks. Each one is a real, separable module.
# ---------------------------------------------------------------------------


def logging_pre_hook(event: PreToolUseEvent) -> None:
    """Print a one-line trace of every tool call before it runs."""
    short_input = _short(event.tool_input)
    print(f"[hook][pre]  {event.tool_name}({short_input})")


def logging_post_hook(event: PostToolUseEvent) -> None:
    """Print a one-line trace after the tool returns."""
    status = "ERR" if event.is_error else "OK"
    preview = event.tool_result.splitlines()[0] if event.tool_result else ""
    if len(preview) > 100:
        preview = preview[:100] + "..."
    print(f"[hook][post] {event.tool_name} -> {status} | {preview}")


def logging_complete_hook(event: TaskCompleteEvent) -> None:
    print(
        f"[hook][done] steps={event.step_count} tool_calls={event.tool_call_count}"
    )


def safety_pre_hook(event: PreToolUseEvent) -> None:
    """Block obviously dangerous code patterns before they hit the sandbox.

    The sandbox itself is the real boundary; this hook is defense-in-depth
    and gives Claude an early, descriptive refusal so it can re-plan.
    """
    if event.tool_name != "code_runner":
        return
    code = event.tool_input.get("code", "")
    forbidden = [
        ("import socket", "network access is forbidden in the sandbox"),
        ("urllib.request", "network access is forbidden in the sandbox"),
        ("import requests", "network access is forbidden in the sandbox"),
        ("subprocess", "spawning subprocesses is forbidden in the sandbox"),
        ("os.system", "shell escape is forbidden in the sandbox"),
        ("__import__('os').system", "shell escape is forbidden in the sandbox"),
    ]
    for needle, reason in forbidden:
        if needle in code:
            raise ToolBlocked(f"Pattern '{needle}' is forbidden: {reason}")


def truncate_post_hook(max_chars: int = 8000) -> PostHook:
    """Cap tool result size so the conversation context stays bounded."""

    def hook(event: PostToolUseEvent) -> None:
        if len(event.tool_result) > max_chars:
            head = event.tool_result[:max_chars]
            event.tool_result = (
                head + f"\n...[truncated {len(event.tool_result) - max_chars} chars by hook]"
            )

    return hook


def _short(d: dict[str, Any], limit: int = 80) -> str:
    parts = []
    for k, v in d.items():
        sv = repr(v) if not isinstance(v, str) else repr(v[:60] + "..." if len(v) > 60 else v)
        parts.append(f"{k}={sv}")
    out = ", ".join(parts)
    return out if len(out) <= limit else out[:limit] + "..."
