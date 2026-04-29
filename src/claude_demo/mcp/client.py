"""Minimal MCP client.

Speaks JSON-RPC 2.0 to an MCP-shaped peer. The default transport is an
in-process callable that hands the envelope to a ``StubMCPServer.handle``
method — the same wire shape a real stdio/HTTP transport would carry,
just without the bytes on the wire.

Public API:

  client = MCPClient(transport=server.handle)
  client.initialize()
  tools = client.list_tools()              # list[dict]
  result = client.call_tool("read_text", {"path": "README.md"})
"""

from __future__ import annotations

import itertools
from typing import Any, Callable

JSONRPC_VERSION = "2.0"

Transport = Callable[[dict[str, Any]], dict[str, Any]]


class MCPError(RuntimeError):
    """Raised when an MCP peer returns a JSON-RPC error response."""

    def __init__(self, code: int, message: str) -> None:
        super().__init__(f"[{code}] {message}")
        self.code = code
        self.message = message


class MCPClient:
    """JSON-RPC client. Transport is any callable that takes an envelope
    and returns a response envelope. Replace with a stdio/HTTP-backed one
    in production without changing this class."""

    def __init__(self, transport: Transport, *, server_name: str = "stub") -> None:
        self._transport = transport
        self._server_name = server_name
        self._ids = itertools.count(1)
        self._initialized = False

    @property
    def server_name(self) -> str:
        return self._server_name

    def initialize(self) -> dict[str, Any]:
        result = self._call("initialize", {"protocolVersion": "2024-11-05"})
        self._initialized = True
        return result

    def list_tools(self) -> list[dict[str, Any]]:
        if not self._initialized:
            self.initialize()
        result = self._call("tools/list", {})
        return result.get("tools", [])

    def call_tool(self, name: str, arguments: dict[str, Any] | None = None) -> str:
        if not self._initialized:
            self.initialize()
        result = self._call("tools/call", {"name": name, "arguments": arguments or {}})
        # MCP returns content blocks; collapse to a string for our Tool surface.
        chunks = []
        for block in result.get("content", []):
            if block.get("type") == "text":
                chunks.append(block.get("text", ""))
        is_error = bool(result.get("isError"))
        text = "\n".join(chunks)
        return f"ERROR: {text}" if is_error and not text.startswith("ERROR:") else text

    def _call(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        envelope = {
            "jsonrpc": JSONRPC_VERSION,
            "id": next(self._ids),
            "method": method,
            "params": params,
        }
        response = self._transport(envelope)
        if "error" in response:
            err = response["error"]
            raise MCPError(int(err.get("code", -32000)), str(err.get("message", "unknown")))
        return response.get("result") or {}
