"""Policy evaluator — bridges a ``Policy`` into a PreToolUse hook.

The hook fires before every tool dispatch and applies three checks:

  1. Tool-level decision (allow / confirm / deny)
  2. ``code_runner`` input scanned for forbidden code patterns
  3. (HTTP host allowlist is enforced inside the credential proxy itself,
     using ``Policy.http_allowlist``. Putting that check there keeps the
     egress chokepoint authoritative — even if a future tool bypasses
     the policy hook, it still has to go through the proxy.)

Why three layers (policy / proxy / sandbox) instead of one: defence in
depth. Any single layer can have a bug; an attacker has to defeat all
three to escape the runtime.
"""

from __future__ import annotations

from typing import Callable

from ..core.hooks import PreToolUseEvent, ToolBlocked
from ..core.permissions import Decision
from .schema import Policy

PreHook = Callable[[PreToolUseEvent], None]
ConfirmFn = Callable[[str, dict], bool]


def _default_confirm(tool_name: str, tool_input: dict) -> bool:
    """Stdin y/N prompt. Replace with a UI/webhook in production."""
    short = ", ".join(f"{k}={v!r}" for k, v in tool_input.items())
    if len(short) > 80:
        short = short[:80] + "..."
    print(f"\n[policy.confirm] tool='{tool_name}' input=({short})")
    answer = input("Allow? [y/N] ").strip().lower()
    return answer in ("y", "yes")


def as_pre_hook(policy: Policy, *, confirm_fn: ConfirmFn | None = None) -> PreHook:
    """Build the PreToolUse hook that enforces ``policy``."""
    confirm = confirm_fn or _default_confirm

    def hook(event: PreToolUseEvent) -> None:
        # 1. Tool-level decision
        verdict = policy.decide(event.tool_name)
        if verdict is Decision.DENY:
            raise ToolBlocked(
                f"policy '{policy.name}' denies tool '{event.tool_name}'"
            )
        if verdict is Decision.CONFIRM:
            if not confirm(event.tool_name, event.tool_input):
                raise ToolBlocked(
                    f"operator declined tool '{event.tool_name}' under policy '{policy.name}'"
                )

        # 2. Code-pattern check for code_runner
        if event.tool_name == "code_runner":
            code = event.tool_input.get("code", "")
            if isinstance(code, str):
                for forbidden in policy.forbidden_code_patterns:
                    if forbidden.pattern in code:
                        raise ToolBlocked(
                            f"policy '{policy.name}' forbids pattern "
                            f"'{forbidden.pattern}': {forbidden.reason}"
                        )

    return hook
