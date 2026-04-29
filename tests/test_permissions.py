"""Tests for the permission policy."""

from __future__ import annotations

import unittest

from src.hooks import HookEngine, PreToolUseEvent
from src.permissions import Decision, PermissionPolicy


class TestPermissionPolicy(unittest.TestCase):
    def test_default_allow(self) -> None:
        policy = PermissionPolicy(default=Decision.ALLOW)
        engine = HookEngine().add_pre(policy.as_pre_hook())
        event = PreToolUseEvent("code_runner", {"code": "print(1)"})
        engine.fire_pre(event)
        self.assertFalse(event.blocked)

    def test_default_deny(self) -> None:
        policy = PermissionPolicy(default=Decision.DENY)
        engine = HookEngine().add_pre(policy.as_pre_hook())
        event = PreToolUseEvent("code_runner", {"code": "print(1)"})
        engine.fire_pre(event)
        self.assertTrue(event.blocked)
        self.assertIn("denies", event.block_reason)

    def test_per_tool_override(self) -> None:
        policy = PermissionPolicy(
            default=Decision.ALLOW,
            overrides={"code_runner": Decision.DENY},
        )
        engine = HookEngine().add_pre(policy.as_pre_hook())

        deny_event = PreToolUseEvent("code_runner", {})
        engine.fire_pre(deny_event)
        self.assertTrue(deny_event.blocked)

        allow_event = PreToolUseEvent("file_tool", {"op": "list"})
        engine.fire_pre(allow_event)
        self.assertFalse(allow_event.blocked)

    def test_confirm_yes(self) -> None:
        confirms: list[tuple[str, dict]] = []

        def confirm(name: str, inp: dict) -> bool:
            confirms.append((name, inp))
            return True

        policy = PermissionPolicy(
            default=Decision.CONFIRM,
            confirm_fn=confirm,
        )
        engine = HookEngine().add_pre(policy.as_pre_hook())
        event = PreToolUseEvent("code_runner", {"code": "print(1)"})
        engine.fire_pre(event)
        self.assertFalse(event.blocked)
        self.assertEqual(confirms, [("code_runner", {"code": "print(1)"})])

    def test_confirm_no(self) -> None:
        policy = PermissionPolicy(
            default=Decision.CONFIRM,
            confirm_fn=lambda name, inp: False,
        )
        engine = HookEngine().add_pre(policy.as_pre_hook())
        event = PreToolUseEvent("code_runner", {})
        engine.fire_pre(event)
        self.assertTrue(event.blocked)
        self.assertIn("declined", event.block_reason)


if __name__ == "__main__":
    unittest.main()
