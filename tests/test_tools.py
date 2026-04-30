"""Tests for the tool layer."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from claude_demo.sandbox import Sandbox
from claude_demo.tools import make_code_runner, make_file_tool


class TestCodeRunner(unittest.TestCase):
    def test_runs_python_via_sandbox(self) -> None:
        sandbox = Sandbox(timeout_seconds=5.0)
        tool = make_code_runner(sandbox)
        out = tool.run({"code": "print(7*6)"})
        self.assertIn("42", out)

    def test_rejects_empty_code(self) -> None:
        sandbox = Sandbox(timeout_seconds=5.0)
        tool = make_code_runner(sandbox)
        out = tool.run({"code": ""})
        self.assertTrue(out.startswith("ERROR:"))


class TestFileTool(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.input_dir = Path(self.tmp.name)
        (self.input_dir / "a.txt").write_text("alpha\nbeta\ngamma\n", encoding="utf-8")
        (self.input_dir / "b.csv").write_text("h1,h2\n1,2\n", encoding="utf-8")
        self.tool = make_file_tool(self.input_dir)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_list(self) -> None:
        out = self.tool.run({"op": "list"})
        self.assertIn("a.txt", out)
        self.assertIn("b.csv", out)

    def test_stat(self) -> None:
        out = self.tool.run({"op": "stat", "name": "a.txt"})
        self.assertIn("a.txt", out)
        self.assertIn("is_file=True", out)

    def test_head(self) -> None:
        out = self.tool.run({"op": "head", "name": "a.txt", "lines": 2})
        self.assertIn("alpha", out)
        self.assertIn("beta", out)
        self.assertNotIn("gamma", out)

    def test_read(self) -> None:
        out = self.tool.run({"op": "read", "name": "b.csv"})
        self.assertIn("h1,h2", out)
        self.assertIn("1,2", out)

    def test_path_traversal_rejected(self) -> None:
        out = self.tool.run({"op": "read", "name": "../etc/passwd"})
        self.assertTrue(out.startswith("ERROR:"))
        self.assertIn("outside", out)

    def test_unknown_op(self) -> None:
        out = self.tool.run({"op": "delete", "name": "a.txt"})
        self.assertTrue(out.startswith("ERROR:"))

    def test_missing_file(self) -> None:
        out = self.tool.run({"op": "read", "name": "missing.txt"})
        self.assertTrue(out.startswith("ERROR:"))

    def test_write_rejected_when_not_writable(self) -> None:
        # Default file_tool is read-only; write attempts are rejected.
        out = self.tool.run({"op": "write", "name": "x.txt", "content": "hi"})
        self.assertTrue(out.startswith("ERROR:"))
        self.assertIn("read-only", out)


class TestFileToolWritable(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.workspace = Path(self.tmp.name)
        self.tool = make_file_tool(self.workspace, writable=True)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_write_creates_file(self) -> None:
        out = self.tool.run(
            {"op": "write", "name": "out.md", "content": "# hello\n"}
        )
        self.assertIn("OK write", out)
        self.assertEqual(
            (self.workspace / "out.md").read_text(encoding="utf-8"), "# hello\n"
        )

    def test_write_path_traversal_rejected(self) -> None:
        out = self.tool.run(
            {"op": "write", "name": "../escape.txt", "content": "x"}
        )
        self.assertTrue(out.startswith("ERROR:"))
        self.assertIn("outside", out)

    def test_append_extends_file(self) -> None:
        self.tool.run({"op": "write", "name": "log.txt", "content": "first\n"})
        self.tool.run({"op": "append", "name": "log.txt", "content": "second\n"})
        body = (self.workspace / "log.txt").read_text(encoding="utf-8")
        self.assertEqual(body, "first\nsecond\n")

    def test_write_into_subdirectory(self) -> None:
        # Subdirectories are created on demand.
        out = self.tool.run(
            {"op": "write", "name": "notes/a.md", "content": "x"}
        )
        self.assertIn("OK write", out)
        self.assertTrue((self.workspace / "notes" / "a.md").exists())

    def test_write_requires_string_content(self) -> None:
        out = self.tool.run({"op": "write", "name": "x.txt", "content": 42})
        self.assertTrue(out.startswith("ERROR:"))


if __name__ == "__main__":
    unittest.main()
