"""Per-tool permission policy.

Each tool gets a verdict from the policy before its PreToolUse hooks run.
Three outcomes:

  ALLOW    - run silently
  CONFIRM  - prompt the operator (CLI y/N) before running
  DENY     - refuse without running

The policy is plugged in as a PreToolUse hook by the agent runner. This
keeps it composable with logging / safety hooks — order of registration
controls priority.

This is the "tool permission system" the spec calls out as a strong
signal. It's intentionally tiny: declarative defaults + per-tool overrides
+ a confirmation callback you can swap (CLI today, web UI tomorrow).
"""

from __future__ import annotations

import enum
from dataclasses import dataclass
from typing import Callable

from .hooks import PreToolUseEvent, ToolBlocked


class Decision(enum.Enum):
    ALLOW = "allow"
    CONFIRM = "confirm"
    DENY = "deny"


# Callback used when a CONFIRM decision needs operator input. The default
# CLI implementation reads y/N from stdin; tests inject a stub.
ConfirmFn = Callable[[str, dict], bool]


def cli_confirm(tool_name: str, tool_input: dict) -> bool:
    """Default operator prompt: ask y/N on stdin."""
    short = ", ".join(f"{k}={_short_value(v)}" for k, v in tool_input.items())
    print(f"\n[confirm] Tool '{tool_name}' wants to run with: {short}")
    answer = input("Allow? [y/N] ").strip().lower()
    return answer in ("y", "yes")


def _short_value(v: object, limit: int = 60) -> str:
    s = repr(v)
    return s if len(s) <= limit else s[:limit] + "..."


@dataclass
class PermissionPolicy:
    """Policy table: per-tool verdict, plus a default fallback."""

    default: Decision = Decision.ALLOW
    overrides: dict[str, Decision] | None = None
    confirm_fn: ConfirmFn = cli_confirm

    def decide(self, tool_name: str) -> Decision:
        if self.overrides and tool_name in self.overrides:
            return self.overrides[tool_name]
        return self.default

    def as_pre_hook(self):
        """Return a PreToolUse hook that enforces this policy."""

        def hook(event: PreToolUseEvent) -> None:
            verdict = self.decide(event.tool_name)
            if verdict is Decision.DENY:
                raise ToolBlocked(
                    f"Permission policy denies tool '{event.tool_name}'"
                )
            if verdict is Decision.CONFIRM:
                ok = self.confirm_fn(event.tool_name, event.tool_input)
                if not ok:
                    raise ToolBlocked(
                        f"Operator declined to run tool '{event.tool_name}'"
                    )
            # ALLOW: no-op

        return hook
