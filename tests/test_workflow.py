"""Tests for the workflow primitive."""

from __future__ import annotations

import unittest

from claude_demo.core.workflow import Workflow


class TestWorkflow(unittest.TestCase):
    def test_steps_run_in_order_and_share_context(self) -> None:
        wf = (
            Workflow("demo")
            .step("a", lambda ctx: ctx["x"] + 1, output_key="a")
            .step("b", lambda ctx: ctx["a"] * 2, output_key="b")
        )
        run = wf.run({"x": 10})
        self.assertEqual(run.context["a"], 11)
        self.assertEqual(run.context["b"], 22)
        self.assertEqual([s for s, _ in run.step_log], ["a", "b"])
        self.assertTrue(all(status == "ok" for _, status in run.step_log))

    def test_stops_on_first_error(self) -> None:
        def boom(ctx: dict) -> int:
            raise RuntimeError("boom")

        wf = (
            Workflow("demo")
            .step("ok", lambda ctx: 1, output_key="ok")
            .step("fail", boom, output_key="fail")
            .step("never", lambda ctx: 99, output_key="never")
        )
        run = wf.run()
        self.assertEqual(run.context.get("ok"), 1)
        self.assertNotIn("fail", run.context)
        self.assertNotIn("never", run.context)
        self.assertEqual(run.step_log[0], ("ok", "ok"))
        self.assertEqual(run.step_log[1][0], "fail")
        self.assertTrue(run.step_log[1][1].startswith("error:"))


if __name__ == "__main__":
    unittest.main()
