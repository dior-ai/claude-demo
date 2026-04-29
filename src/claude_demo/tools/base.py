"""Tool dataclass — the surface every tool exposes to runners.

The runtime treats every callable as a `Tool` regardless of where it
came from: handwritten code, MCP server, browser automation, etc.
This file owns only the shape; concrete tools live in sibling modules.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable


@dataclass
class Tool:
    """LLM-facing tool definition.

    A tool is a triple: schema (so an LLM knows how to call it), description
    (so it knows when), and a `run` callable (so the runtime can execute it).
    Tools are dispatched by the agent loop; hooks intercept before/after each
    `.run()` call.
    """

    name: str
    description: str
    input_schema: dict[str, Any]
    run: Callable[[dict[str, Any]], str]

    def schema(self) -> dict[str, Any]:
        """Render to the Anthropic Messages API ``tools=`` shape."""
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": self.input_schema,
        }
