"""Tests for the sandbox executor.

Run with `python -m unittest discover tests` (no extra deps required).
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from claude_demo.sandbox import Sandbox


class TestSandbox(unittest.TestCase):
    def test_basic_print(self) -> None:
        sb = Sandbox(timeout_seconds=5.0)
        result = sb.run_python("print(2 + 2)")
        self.assertTrue(result.ok, result.summary())
        self.assertIn("4", result.stdout)
        self.assertEqual(result.return_code, 0)
        self.assertFalse(result.timed_out)

    def test_nonzero_exit(self) -> None:
        sb = Sandbox(timeout_seconds=5.0)
        result = sb.run_python("import sys; sys.exit(7)")
        self.assertFalse(result.ok)
        self.assertEqual(result.return_code, 7)

    def test_timeout(self) -> None:
        sb = Sandbox(timeout_seconds=1.0)
        result = sb.run_python("import time; time.sleep(5)")
        self.assertTrue(result.timed_out)
        self.assertFalse(result.ok)

    def test_input_file_is_visible(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            data = Path(tmp) / "data.txt"
            data.write_text("hello-from-host", encoding="utf-8")
            sb = Sandbox(timeout_seconds=5.0, input_files={"data.txt": data})
            result = sb.run_python(
                "print(open('data.txt', encoding='utf-8').read())"
            )
            self.assertTrue(result.ok, result.summary())
            self.assertIn("hello-from-host", result.stdout)

    def test_input_file_is_isolated(self) -> None:
        # Writes inside the sandbox must not affect the host original.
        with tempfile.TemporaryDirectory() as tmp:
            data = Path(tmp) / "data.txt"
            data.write_text("original", encoding="utf-8")
            sb = Sandbox(timeout_seconds=5.0, input_files={"data.txt": data})
            sb.run_python(
                "open('data.txt', 'w', encoding='utf-8').write('overwritten')"
            )
            self.assertEqual(data.read_text(encoding="utf-8"), "original")


if __name__ == "__main__":
    unittest.main()
