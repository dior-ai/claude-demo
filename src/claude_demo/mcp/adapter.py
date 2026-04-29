"""Wrap MCP tools as platform ``Tool`` objects.

Once an MCPClient has listed its tools, the runtime needs them to look
like the rest of its tool surface — a ``Tool`` dataclass with a JSON
schema and a ``run(input)`` callable. ``wrap_as_tools`` produces those.

Naming: MCP tools are prefixed ``mcp__<server>__<tool>`` to match the
convention used by Claude Code and other MCP-aware runners. That makes
them visually distinct in audit logs and hook traces, and avoids name
clashes with first-party tools.
"""

from __future__ import annotations

from typing import Any

from ..tools.base import Tool
from .client import MCPClient, MCPError


def _make_run(client: MCPClient, mcp_name: str):
    def run(tool_input: dict[str, Any]) -> str:
        try:
            return client.call_tool(mcp_name, tool_input)
        except MCPError as exc:
            return f"ERROR: MCP call failed: {exc}"

    return run


def wrap_as_tools(client: MCPClient) -> list[Tool]:
    """List the client's tools and return platform ``Tool`` objects."""
    wrapped: list[Tool] = []
    for spec in client.list_tools():
        mcp_name = spec.get("name", "")
        if not mcp_name:
            continue
        platform_name = f"mcp__{client.server_name}__{mcp_name}"
        wrapped.append(
            Tool(
                name=platform_name,
                description=spec.get("description", ""),
                input_schema=spec.get("inputSchema") or {"type": "object"},
                run=_make_run(client, mcp_name),
            )
        )
    return wrapped
