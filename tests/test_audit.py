"""Tests for the JSONL audit log and its hook adapters."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from claude_demo.audit.log import (
    EVENT_POST_TOOL_USE,
    EVENT_PRE_TOOL_USE,
    EVENT_RUN_START,
    EVENT_TASK_COMPLETE,
    AuditLog,
)
from claude_demo.core.hooks import (
    HookEngine,
    PostToolUseEvent,
    PreToolUseEvent,
    TaskCompleteEvent,
)


def _read_records(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


class TestAuditLog(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_emits_run_start_with_metadata(self) -> None:
        with AuditLog.for_run("run_1", base_dir=self.base) as log:
            log.emit_run_start(user_input="hi", policy_name="default")
        records = _read_records(self.base / "run_1.jsonl")
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["event"], EVENT_RUN_START)
        self.assertEqual(records[0]["payload"]["user_input"], "hi")
        self.assertEqual(records[0]["payload"]["policy"], "default")

    def test_jsonl_records_are_well_formed(self) -> None:
        with AuditLog.for_run("run_2", base_dir=self.base) as log:
            log.emit("custom", actor="test", custom_field=42)
        records = _read_records(self.base / "run_2.jsonl")
        self.assertEqual(len(records), 1)
        rec = records[0]
        for required in ("ts", "run_id", "event", "actor", "step_id", "correlation_id", "payload"):
            self.assertIn(required, rec)
        self.assertEqual(rec["payload"]["custom_field"], 42)
        # ts should be ISO-shaped
        self.assertRegex(rec["ts"], r"^\d{4}-\d{2}-\d{2}T")

    def test_pre_post_hooks_correlate(self) -> None:
        with AuditLog.for_run("run_3", base_dir=self.base) as log:
            engine = HookEngine().add_pre(log.as_pre_hook()).add_post(log.as_post_hook())
            pre = PreToolUseEvent("tool_a", {"x": 1})
            engine.fire_pre(pre)
            engine.fire_post(
                PostToolUseEvent(tool_name="tool_a", tool_input={"x": 1}, tool_result="ok")
            )
        records = _read_records(self.base / "run_3.jsonl")
        self.assertEqual([r["event"] for r in records], [EVENT_PRE_TOOL_USE, EVENT_POST_TOOL_USE])
        self.assertEqual(records[0]["correlation_id"], records[1]["correlation_id"])
        self.assertEqual(records[0]["step_id"], records[1]["step_id"])
        self.assertNotEqual(records[0]["correlation_id"], "")

    def test_step_counter_increments(self) -> None:
        with AuditLog.for_run("run_4", base_dir=self.base) as log:
            engine = HookEngine().add_pre(log.as_pre_hook())
            for i in range(3):
                engine.fire_pre(PreToolUseEvent(f"tool_{i}", {}))
        records = _read_records(self.base / "run_4.jsonl")
        self.assertEqual([r["step_id"] for r in records], [1, 2, 3])

    def test_complete_hook_emits_task_complete(self) -> None:
        with AuditLog.for_run("run_5", base_dir=self.base) as log:
            engine = HookEngine().add_complete(log.as_complete_hook())
            engine.fire_complete(
                TaskCompleteEvent(final_text="all done", step_count=2, tool_call_count=3)
            )
        records = _read_records(self.base / "run_5.jsonl")
        self.assertEqual(records[0]["event"], EVENT_TASK_COMPLETE)
        self.assertEqual(records[0]["payload"]["step_count"], 2)
        self.assertEqual(records[0]["payload"]["tool_call_count"], 3)

    def test_close_is_idempotent(self) -> None:
        log = AuditLog.for_run("run_6", base_dir=self.base)
        log.close()
        log.close()  # must not raise


if __name__ == "__main__":
    unittest.main()
