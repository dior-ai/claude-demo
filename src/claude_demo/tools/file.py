"""``file_tool`` — gated filesystem ops on a fixed workspace directory.

Path traversal is blocked: any name resolving outside the directory is
rejected. The tool exposes up to six ops:

  list, stat, head, read         — always available
  write, append                  — only when constructed ``writable=True``

No delete, no execute — the directory is the boundary, and writes
require an explicit opt-in by the caller (research-assistant turns it
on; the cred-safety / red-team substrate keeps it off).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .base import Tool


def make_file_tool(input_dir: Path, *, writable: bool = False) -> Tool:
    """Tool factory: bind a workspace directory to a Tool.

    ``writable=False`` (default) — read-only behaviour preserved for
    the existing scripted demos and the red-team suite.
    ``writable=True`` — adds ``write`` and ``append`` ops so an agent
    can produce output into the same path-safe boundary.
    """
    input_dir = input_dir.resolve()

    def _safe_path(name: str) -> Path:
        candidate = (input_dir / name).resolve()
        try:
            candidate.relative_to(input_dir)
        except ValueError as exc:
            raise PermissionError(
                f"path '{name}' resolves outside the input directory"
            ) from exc
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
            except PermissionError as exc:
                return f"ERROR: {exc}"
            if not p.exists():
                return f"ERROR: '{name}' does not exist"
            return f"name={p.name} size={p.stat().st_size} is_file={p.is_file()}"

        if op == "head":
            name = tool_input.get("name", "")
            n_lines = int(tool_input.get("lines", 20))
            try:
                p = _safe_path(name)
            except PermissionError as exc:
                return f"ERROR: {exc}"
            if not p.is_file():
                return f"ERROR: '{name}' is not a file"
            try:
                with p.open("r", encoding="utf-8", errors="replace") as fh:
                    head_lines = [next(fh, None) for _ in range(n_lines)]
                head = "".join(line for line in head_lines if line is not None)
                return head if head else "(empty file)"
            except OSError as exc:
                return f"ERROR: {exc}"

        if op == "read":
            name = tool_input.get("name", "")
            try:
                p = _safe_path(name)
            except PermissionError as exc:
                return f"ERROR: {exc}"
            if not p.is_file():
                return f"ERROR: '{name}' is not a file"
            try:
                data = p.read_text(encoding="utf-8", errors="replace")
            except OSError as exc:
                return f"ERROR: {exc}"
            limit = 16_000
            if len(data) > limit:
                return (
                    data[:limit]
                    + f"\n...[truncated {len(data) - limit} chars; use code_runner for large files]"
                )
            return data

        if op in ("write", "append"):
            if not writable:
                return (
                    f"ERROR: op '{op}' is not allowed "
                    "(file_tool was constructed read-only)"
                )
            name = tool_input.get("name", "")
            content = tool_input.get("content", "")
            if not isinstance(content, str):
                return "ERROR: 'content' must be a string"
            try:
                p = _safe_path(name)
            except PermissionError as exc:
                return f"ERROR: {exc}"
            if p.exists() and p.is_dir():
                return f"ERROR: '{name}' is a directory"
            try:
                p.parent.mkdir(parents=True, exist_ok=True)
                if op == "write":
                    p.write_text(content, encoding="utf-8")
                else:
                    with p.open("a", encoding="utf-8") as fh:
                        fh.write(content)
            except OSError as exc:
                return f"ERROR: {exc}"
            return f"OK {op} {p.name} ({len(content)} chars)"

        valid = "list, stat, head, read" + (
            ", write, append" if writable else ""
        )
        return f"ERROR: unknown op '{op}'. Valid ops: {valid}."

    ops_enum = ["list", "stat", "head", "read"]
    if writable:
        ops_enum = ops_enum + ["write", "append"]

    if writable:
        description = (
            "Filesystem ops on the workspace directory. Read ops: list "
            "(no args), stat/head/read (require 'name'). Write ops: "
            "write/append (require 'name' and 'content'). Path "
            "traversal outside the workspace is rejected."
        )
    else:
        description = (
            "Read-only filesystem inspection of the input directory. "
            "Operations: list (no args), stat/head/read (require 'name'). "
            "Path traversal outside the input directory is rejected."
        )

    return Tool(
        name="file_tool",
        description=description,
        input_schema={
            "type": "object",
            "properties": {
                "op": {
                    "type": "string",
                    "enum": ops_enum,
                    "description": "The operation to perform.",
                },
                "name": {
                    "type": "string",
                    "description": "File name within the workspace directory.",
                },
                "lines": {
                    "type": "integer",
                    "description": "For op=head: number of lines to return (default 20).",
                    "default": 20,
                },
                "content": {
                    "type": "string",
                    "description": (
                        "Text body for op=write or op=append. "
                        "Ignored for other ops."
                    ),
                },
            },
            "required": ["op"],
        },
        run=run,
    )
