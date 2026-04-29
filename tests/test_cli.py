"""Smoke tests for the CLI surface — `python -m claude_demo`.

Test by invoking ``main(argv)`` directly so we don't fork a subprocess
on every test. Output is captured via ``rich.Console`` redirect.
"""

from __future__ import annotations

import io
import json
import tempfile
import unittest
from pathlib import Path

from claude_demo.cli.__main__ import main as cli_main


class TestAuditView(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "run_test.jsonl"
        records = [
            {
                "ts": "2026-04-29T12:00:00.000000+00:00",
                "run_id": "run_test",
                "event": "pre_tool_use",
                "actor": "hook_engine",
                "correlation_id": "corr_aaa",
                "step_id": 1,
                "payload": {"tool": "http_request", "input_keys": ["url"]},
            },
            {
                "ts": "2026-04-29T12:00:00.100000+00:00",
                "run_id": "run_test",
                "event": "post_tool_use",
                "actor": "hook_engine",
                "correlation_id": "corr_aaa",
                "step_id": 1,
                "payload": {"tool": "http_request", "is_error": False, "result_size": 42},
            },
            {
                "ts": "2026-04-29T12:00:00.200000+00:00",
                "run_id": "run_test",
                "event": "task_complete",
                "actor": "hook_engine",
                "correlation_id": "",
                "step_id": 0,
                "payload": {"step_count": 1, "tool_call_count": 1},
            },
        ]
        with self.path.open("w", encoding="utf-8") as fh:
            for rec in records:
                fh.write(json.dumps(rec) + "\n")

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _run(self, argv: list[str]) -> tuple[int, str]:
        # Capture rich Console output by redirecting its file. Force a wide
        # terminal so the table doesn't truncate event names mid-string.
        from claude_demo.ui import console as ui_console

        buf = io.StringIO()
        original_file = ui_console._console.file
        original_width = ui_console._console.width
        ui_console._console.file = buf
        ui_console._console._width = 200  # bypass terminal-size detection
        try:
            rc = cli_main(argv)
        finally:
            ui_console._console.file = original_file
            ui_console._console._width = original_width
        return rc, buf.getvalue()

    def test_view_unfiltered(self) -> None:
        rc, out = self._run(["audit", "view", str(self.path)])
        self.assertEqual(rc, 0)
        self.assertIn("pre_tool_use", out)
        self.assertIn("post_tool_use", out)
        self.assertIn("task_complete", out)
        self.assertIn("3 record(s) shown of 3", out)

    def test_view_filter_event(self) -> None:
        rc, out = self._run(["audit", "view", str(self.path), "--filter-event", "task_complete"])
        self.assertEqual(rc, 0)
        self.assertIn("task_complete", out)
        self.assertIn("1 record(s) shown of 3", out)
        # Filtered-out records' event names shouldn't appear in the table body.
        self.assertNotIn("pre_tool_use", out)
        self.assertNotIn("post_tool_use", out)

    def test_view_filter_correlation(self) -> None:
        rc, out = self._run(["audit", "view", str(self.path), "--filter-correlation", "corr_aaa"])
        self.assertEqual(rc, 0)
        self.assertIn("2 record(s) shown of 3", out)

    def test_view_missing_file_exits(self) -> None:
        with self.assertRaises(SystemExit):
            self._run(["audit", "view", "/no/such/path.jsonl"])


if __name__ == "__main__":
    unittest.main()
