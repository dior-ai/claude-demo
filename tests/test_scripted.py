"""Tests for the ScriptedRunner."""

from __future__ import annotations

import unittest

from claude_demo.core.hooks import HookEngine, PreToolUseEvent, ToolBlocked
from claude_demo.agents.scripted import ScriptedPlan, ScriptedRunner
from claude_demo.tools.base import Tool


def _echo_tool() -> Tool:
    return Tool(
        name="echo",
        description="returns its 'msg' input",
        input_schema={"type": "object", "properties": {"msg": {"type": "string"}}, "required": ["msg"]},
        run=lambda inp: f"echo:{inp.get('msg', '')}",
    )


def _failing_tool() -> Tool:
    def run(inp):
        raise RuntimeError("intentional failure")

    return Tool(
        name="failing",
        description="raises",
        input_schema={"type": "object", "properties": {}},
        run=run,
    )


class TestScriptedRunner(unittest.TestCase):
    def test_runs_plan_in_order_and_records_state(self) -> None:
        hooks = HookEngine()
        runner = ScriptedRunner(tools=[_echo_tool()], hooks=hooks)
        plan = (
            ScriptedPlan(final_text="done")
            .add("echo", {"msg": "first"}, rationale="a")
            .add("echo", {"msg": "second"}, rationale="b")
        )
        state = runner.run("u", plan)
        self.assertEqual(state.step_count, 2)
        self.assertEqual(state.tool_call_count, 2)
        self.assertEqual(state.steps[0].tool_calls[0].result, "echo:first")
        self.assertEqual(state.steps[1].tool_calls[0].result, "echo:second")
        self.assertEqual(state.final_text, "done")

    def test_pre_hook_can_block(self) -> None:
        def deny_echo(event: PreToolUseEvent) -> None:
            if event.tool_name == "echo":
                raise ToolBlocked("nope")

        hooks = HookEngine().add_pre(deny_echo)
        runner = ScriptedRunner(tools=[_echo_tool()], hooks=hooks)
        plan = ScriptedPlan().add("echo", {"msg": "x"})
        state = runner.run("u", plan)
        call = state.steps[0].tool_calls[0]
        self.assertTrue(call.blocked)
        self.assertEqual(call.block_reason, "nope")
        self.assertTrue(call.is_error)

    def test_tool_exception_is_caught_and_marked_error(self) -> None:
        runner = ScriptedRunner(tools=[_failing_tool()], hooks=HookEngine())
        plan = ScriptedPlan().add("failing", {})
        state = runner.run("u", plan)
        call = state.steps[0].tool_calls[0]
        self.assertTrue(call.is_error)
        self.assertIn("intentional failure", call.result)

    def test_unknown_tool_is_marked_error(self) -> None:
        runner = ScriptedRunner(tools=[], hooks=HookEngine())
        plan = ScriptedPlan().add("missing", {})
        state = runner.run("u", plan)
        call = state.steps[0].tool_calls[0]
        self.assertTrue(call.is_error)
        self.assertIn("unknown tool", call.result)

    def test_complete_hook_fires_once(self) -> None:
        seen = []
        hooks = HookEngine().add_complete(lambda e: seen.append(e))
        runner = ScriptedRunner(tools=[_echo_tool()], hooks=hooks)
        runner.run(
            "u",
            ScriptedPlan(final_text="done").add("echo", {"msg": "x"}),
        )
        self.assertEqual(len(seen), 1)
        self.assertEqual(seen[0].step_count, 1)
        self.assertEqual(seen[0].tool_call_count, 1)


if __name__ == "__main__":
    unittest.main()
