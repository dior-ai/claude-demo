"""Tests for the hook engine and built-in hooks."""

from __future__ import annotations

import unittest

from claude_demo.core.hooks import (
    HookEngine,
    PostToolUseEvent,
    PreToolUseEvent,
    TaskCompleteEvent,
    ToolBlocked,
    safety_pre_hook,
    truncate_post_hook,
)


class TestHookEngine(unittest.TestCase):
    def test_pre_hooks_run_in_order(self) -> None:
        seen: list[str] = []
        engine = HookEngine()
        engine.add_pre(lambda e: seen.append("a"))
        engine.add_pre(lambda e: seen.append("b"))
        engine.fire_pre(PreToolUseEvent("t", {}))
        self.assertEqual(seen, ["a", "b"])

    def test_pre_hook_can_rewrite_input(self) -> None:
        def redact(e: PreToolUseEvent) -> None:
            if "secret" in e.tool_input:
                e.tool_input["secret"] = "<redacted>"

        engine = HookEngine().add_pre(redact)
        event = PreToolUseEvent("t", {"secret": "hunter2"})
        engine.fire_pre(event)
        self.assertEqual(event.tool_input["secret"], "<redacted>")

    def test_pre_hook_blocks_via_exception(self) -> None:
        def deny(e: PreToolUseEvent) -> None:
            raise ToolBlocked("nope")

        engine = HookEngine().add_pre(deny)
        event = PreToolUseEvent("t", {})
        engine.fire_pre(event)
        self.assertTrue(event.blocked)
        self.assertEqual(event.block_reason, "nope")

    def test_pre_hook_blocks_via_flag(self) -> None:
        def deny(e: PreToolUseEvent) -> None:
            e.blocked = True
            e.block_reason = "flagged"

        # Once blocked, subsequent hooks must not run.
        ran_second = []

        def second(e: PreToolUseEvent) -> None:
            ran_second.append(True)

        engine = HookEngine().add_pre(deny).add_pre(second)
        event = PreToolUseEvent("t", {})
        engine.fire_pre(event)
        self.assertTrue(event.blocked)
        self.assertEqual(ran_second, [])

    def test_post_hook_can_rewrite_result(self) -> None:
        engine = HookEngine().add_post(truncate_post_hook(max_chars=10))
        event = PostToolUseEvent(
            tool_name="t",
            tool_input={},
            tool_result="x" * 50,
        )
        engine.fire_post(event)
        self.assertTrue(event.tool_result.startswith("x" * 10))
        self.assertIn("truncated", event.tool_result)

    def test_complete_hook_fires(self) -> None:
        seen: list[TaskCompleteEvent] = []
        engine = HookEngine().add_complete(lambda e: seen.append(e))
        engine.fire_complete(
            TaskCompleteEvent(final_text="done", step_count=3, tool_call_count=5)
        )
        self.assertEqual(len(seen), 1)
        self.assertEqual(seen[0].step_count, 3)


class TestSafetyHook(unittest.TestCase):
    def test_blocks_network_imports(self) -> None:
        event = PreToolUseEvent(
            "code_runner", {"code": "import socket\ns = socket.socket()"}
        )
        with self.assertRaises(ToolBlocked):
            safety_pre_hook(event)

    def test_blocks_subprocess(self) -> None:
        event = PreToolUseEvent(
            "code_runner", {"code": "import subprocess; subprocess.run(['ls'])"}
        )
        with self.assertRaises(ToolBlocked):
            safety_pre_hook(event)

    def test_allows_safe_code(self) -> None:
        event = PreToolUseEvent("code_runner", {"code": "print(1+1)"})
        safety_pre_hook(event)  # should not raise
        self.assertFalse(event.blocked)

    def test_ignores_other_tools(self) -> None:
        # safety_pre_hook only inspects code_runner inputs.
        event = PreToolUseEvent("file_tool", {"op": "list"})
        safety_pre_hook(event)
        self.assertFalse(event.blocked)


if __name__ == "__main__":
    unittest.main()
