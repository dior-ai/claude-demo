"""``code_runner`` — Python execution inside the secure sandbox."""

from __future__ import annotations

from typing import Any

from ..sandbox import Sandbox
from .base import Tool


def make_code_runner(sandbox: Sandbox) -> Tool:
    """Tool factory: bind a sandbox instance to a Tool."""

    def run(tool_input: dict[str, Any]) -> str:
        code = tool_input.get("code", "")
        if not isinstance(code, str) or not code.strip():
            return "ERROR: 'code' must be a non-empty string"
        result = sandbox.run_python(code)
        if result.timed_out:
            # Surface a timeout as an error so the runtime, hooks, and audit
            # all classify it consistently — and so the agent knows to adapt
            # rather than retry the same hung snippet.
            return f"ERROR: sandbox timed out after {result.duration_ms}ms — {result.summary()}"
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
