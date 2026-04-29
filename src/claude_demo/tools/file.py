"""``file_tool`` — read-only filesystem ops on a fixed input directory.

Path traversal is blocked: any name resolving outside the directory is
rejected. The tool exposes four ops: list, stat, head, read. No write,
no delete, no execute — the directory is the boundary.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .base import Tool


def make_file_tool(input_dir: Path) -> Tool:
    """Tool factory: bind an input directory to a Tool."""
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
