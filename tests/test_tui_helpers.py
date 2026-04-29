"""Tests for the TUI's pure helpers.

The Textual app itself can't easily be exercised in a unit test (it
requires a virtual terminal), but the data-shaping functions can —
and they're the part most likely to silently break when the audit log
shape evolves.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from claude_demo.ui.tui.app import (
    _event_color,
    _read_jsonl,
    _short_ts,
    _summarize_run,
)


class TestReadJsonl(unittest.TestCase):
    def test_skips_blank_lines_and_malformed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "log.jsonl"
            path.write_text(
                '{"event":"run_start"}\n'
                "\n"
                "this is not json\n"
                '{"event":"task_complete"}\n',
                encoding="utf-8",
            )
            records = _read_jsonl(path)
        self.assertEqual(len(records), 2)
        self.assertEqual(records[0]["event"], "run_start")
        self.assertEqual(records[1]["event"], "task_complete")

    def test_missing_file_returns_empty(self) -> None:
        self.assertEqual(_read_jsonl(Path("/no/such/file.jsonl")), [])


class TestSummarizeRun(unittest.TestCase):
    def test_counts_tool_calls_and_errors(self) -> None:
        records = [
            {
                "event": "run_start",
                "ts": "2026-04-29T12:00:00.000000+00:00",
                "payload": {"policy": "default"},
            },
            {"event": "pre_tool_use", "payload": {}},
            {"event": "post_tool_use", "payload": {"is_error": False}},
            {"event": "pre_tool_use", "payload": {}},
            {"event": "post_tool_use", "payload": {"is_error": True}},
            {
                "event": "task_complete",
                "ts": "2026-04-29T12:00:01.000000+00:00",
                "payload": {},
            },
        ]
        s = _summarize_run(records)
        self.assertEqual(s["events"], 6)
        self.assertEqual(s["tool_calls"], 2)
        self.assertEqual(s["errors"], 1)
        self.assertEqual(s["policy"], "default")
        self.assertTrue(s["task_complete"])

    def test_handles_incomplete_run(self) -> None:
        records = [{"event": "run_start", "payload": {"policy": "x"}}]
        s = _summarize_run(records)
        self.assertEqual(s["tool_calls"], 0)
        self.assertEqual(s["errors"], 0)
        self.assertFalse(s["task_complete"])


class TestShortTs(unittest.TestCase):
    def test_iso_timestamp(self) -> None:
        out = _short_ts("2026-04-29T12:34:56.789000+00:00")
        self.assertEqual(out, "12:34:56.789")

    def test_invalid_timestamp_falls_back(self) -> None:
        # Should not raise.
        out = _short_ts("not-a-timestamp")
        self.assertIsInstance(out, str)

    def test_empty(self) -> None:
        self.assertEqual(_short_ts(""), "")


class TestEventColor(unittest.TestCase):
    def test_each_known_event_has_a_color(self) -> None:
        for event in (
            "run_start",
            "pre_tool_use",
            "post_tool_use",
            "task_complete",
            "tool_blocked",
            "policy_decision",
        ):
            self.assertIsInstance(_event_color(event), str)

    def test_unknown_event_falls_back(self) -> None:
        self.assertEqual(_event_color("does-not-exist"), "white")


if __name__ == "__main__":
    unittest.main()
