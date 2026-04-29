"""Tool layer the LLM calls.

Two real tools are exposed:

  code_runner  - run Python in the sandbox. Code is exactly what Claude
                 wrote; we don't massage it. Stdout/stderr come back.
  file_tool    - structured file ops on a fixed input directory:
                   list, read, head, stat
                 No write, no delete, no path traversal — the dir is the
                 boundary.

Each tool has:
  .schema       JSON Schema dict for the Anthropic API tools= field
  .run(input)   The actual implementation, returns a string

Tool dispatch happens in the agent loop; hooks intercept before/after each
.run() call.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from .sandbox import Sandbox


@dataclass
class Tool:
    """LLM-facing tool definition."""

    name: str
    description: str
    input_schema: dict[str, Any]
    run: Callable[[dict[str, Any]], str]

    def schema(self) -> dict[str, Any]:
        """Anthropic tool definition format."""
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": self.input_schema,
        }


# ---------------------------------------------------------------------------
# code_runner
# ---------------------------------------------------------------------------


def make_code_runner(sandbox: Sandbox) -> Tool:
    """Tool that runs Python in the given sandbox."""

    def run(tool_input: dict[str, Any]) -> str:
        code = tool_input.get("code", "")
        if not isinstance(code, str) or not code.strip():
            return "ERROR: 'code' must be a non-empty string"
        result = sandbox.run_python(code)
        return result.summary()

    return Tool(
        name="code_runner",
        description=(
            "Execute Python 3 code in a secure ephemeral sandbox. "
            "The cwd contains any input files staged by the runner. "
            "Network access, subprocess spawning, and shell escapes are forbidden. "
            "Print results to stdout — that is the only way to surface output."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "code": {
                    "type": "string",
                    "description": "Python source to execute. Use print() to surface results.",
                },
            },
            "required": ["code"],
        },
        run=run,
    )


# ---------------------------------------------------------------------------
# file_tool
# ---------------------------------------------------------------------------


def make_file_tool(input_dir: Path) -> Tool:
    """Tool for inspecting files in a fixed input directory.

    Path traversal is blocked: any name resolving outside `input_dir` is
    rejected. The tool is read-only.
    """
    input_dir = input_dir.resolve()

    def _safe_path(name: str) -> Path:
        # Reject absolute paths and any traversal that lands outside input_dir.
        candidate = (input_dir / name).resolve()
        try:
            candidate.relative_to(input_dir)
        except ValueError as e:
            raise PermissionError(
                f"path '{name}' resolves outside the input directory"
            ) from e
        return candidate

    def run(tool_input: dict[str, Any]) -> str:
        op = tool_input.get("op")
        if op == "list":
            if not input_dir.exists():
                return "(input directory is empty)"
            entries = sorted(p.name for p in input_dir.iterdir())
            if not entries:
                return "(input directory is empty)"
            return "\n".join(entries)

        if op == "stat":
            name = tool_input.get("name", "")
            try:
                p = _safe_path(name)
            except PermissionError as e:
                return f"ERROR: {e}"
            if not p.exists():
                return f"ERROR: '{name}' does not exist"
            return f"name={p.name} size={p.stat().st_size} is_file={p.is_file()}"

        if op == "head":
            name = tool_input.get("name", "")
            n_lines = int(tool_input.get("lines", 20))
            try:
                p = _safe_path(name)
            except PermissionError as e:
                return f"ERROR: {e}"
            if not p.is_file():
                return f"ERROR: '{name}' is not a file"
            try:
                with p.open("r", encoding="utf-8", errors="replace") as f:
                    head_lines = [next(f, None) for _ in range(n_lines)]
                head = "".join(line for line in head_lines if line is not None)
                return head if head else "(empty file)"
            except OSError as e:
                return f"ERROR: {e}"

        if op == "read":
            name = tool_input.get("name", "")
            try:
                p = _safe_path(name)
            except PermissionError as e:
                return f"ERROR: {e}"
            if not p.is_file():
                return f"ERROR: '{name}' is not a file"
            try:
                data = p.read_text(encoding="utf-8", errors="replace")
            except OSError as e:
                return f"ERROR: {e}"
            # Cap full reads — the LLM should prefer head() for big files.
            limit = 16_000
            if len(data) > limit:
                return (
                    data[:limit]
                    + f"\n...[truncated {len(data) - limit} chars; use code_runner for large files]"
                )
            return data

        return f"ERROR: unknown op '{op}'. Valid ops: list, stat, head, read."

    return Tool(
        name="file_tool",
        description=(
            "Read-only filesystem inspection of the input directory. "
            "Operations: list (no args), stat/head/read (require 'name'). "
            "Path traversal outside the input directory is rejected."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "op": {
                    "type": "string",
                    "enum": ["list", "stat", "head", "read"],
                    "description": "The operation to perform.",
                },
                "name": {
                    "type": "string",
                    "description": "File name within the input directory (required for stat/head/read).",
                },
                "lines": {
                    "type": "integer",
                    "description": "For op=head: number of lines to return (default 20).",
                    "default": 20,
                },
            },
            "required": ["op"],
        },
        run=run,
    )
