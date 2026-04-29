"""In-process stub MCP server.

A real MCP server speaks JSON-RPC 2.0 over stdio (or HTTP/SSE). This stub
implements the same JSON-RPC envelope and the same method names — but
runs in-process so the demo needs no external services or auth.

Methods implemented:

  - ``initialize``               — protocol handshake (no-op shape match)
  - ``tools/list``               — enumerate exposed tools
  - ``tools/call``               — invoke a tool with arguments

The exposed tools demonstrate two enterprise integration shapes:

  - ``read_text``  / ``list_dir`` — filesystem-style server, similar to
    Anthropic's reference filesystem MCP server
  - ``echo``                     — trivial round-trip for tests

This file is the source of truth for the server side; ``client.py``
speaks to it (or to any other JSON-RPC peer) via a transport. The
default in-process transport is just a function call. T2 can swap
that for stdio without touching the client API.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

JSONRPC_VERSION = "2.0"
PROTOCOL_VERSION = "2024-11-05"


@dataclass
class _ToolDef:
    name: str
    description: str
    input_schema: dict[str, Any]
    handler: Callable[[dict[str, Any]], str]


class StubMCPServer:
    """In-process JSON-RPC peer that mimics an MCP server.

    Construct with the root directory it should expose for read-only
    filesystem ops. Path traversal outside the root is rejected.
    """

    def __init__(self, name: str, root: Path | None = None) -> None:
        self.name = name
        self.root = root.resolve() if root else None
        self._tools: dict[str, _ToolDef] = {}
        self._register_default_tools()

    # ------------------------------------------------------------------
    # Tool registration
    # ------------------------------------------------------------------

    def register(
        self,
        name: str,
        description: str,
        input_schema: dict[str, Any],
        handler: Callable[[dict[str, Any]], str],
    ) -> None:
        self._tools[name] = _ToolDef(name, description, input_schema, handler)

    def _register_default_tools(self) -> None:
        self.register(
            name="echo",
            description="Echo back the 'msg' argument. Used to verify connectivity.",
            input_schema={
                "type": "object",
                "properties": {"msg": {"type": "string"}},
                "required": ["msg"],
            },
            handler=lambda args: f"echo:{args.get('msg', '')}",
        )

        if self.root is not None:
            self.register(
                name="list_dir",
                description="List entries in a directory under the server's root.",
                input_schema={
                    "type": "object",
                    "properties": {
                        "path": {
                            "type": "string",
                            "description": "Path relative to the server root.",
                            "default": ".",
                        }
                    },
                },
                handler=self._list_dir,
            )
            self.register(
                name="read_text",
                description="Read a UTF-8 text file under the server's root.",
                input_schema={
                    "type": "object",
                    "properties": {
                        "path": {"type": "string", "description": "Path relative to the server root."}
                    },
                    "required": ["path"],
                },
                handler=self._read_text,
            )

    # ------------------------------------------------------------------
    # JSON-RPC dispatch
    # ------------------------------------------------------------------

    def handle(self, request: dict[str, Any]) -> dict[str, Any]:
        """Dispatch a single JSON-RPC request and return the response envelope."""
        if request.get("jsonrpc") != JSONRPC_VERSION:
            return self._error(request.get("id"), -32600, "Invalid Request: jsonrpc must be '2.0'")

        method = request.get("method")
        params = request.get("params") or {}
        rid = request.get("id")

        try:
            if method == "initialize":
                result = {
                    "protocolVersion": PROTOCOL_VERSION,
                    "serverInfo": {"name": self.name, "version": "0.0.0-stub"},
                    "capabilities": {"tools": {"listChanged": False}},
                }
                return self._ok(rid, result)
            if method == "tools/list":
                return self._ok(rid, {"tools": [self._tool_to_dict(t) for t in self._tools.values()]})
            if method == "tools/call":
                tool_name = params.get("name")
                args = params.get("arguments") or {}
                if tool_name not in self._tools:
                    return self._error(rid, -32601, f"unknown tool: {tool_name}")
                try:
                    content = self._tools[tool_name].handler(args)
                    is_error = content.startswith("ERROR:")
                except PermissionError as exc:
                    # Tool-level failure (e.g., path traversal) — surface as
                    # an isError result, not a JSON-RPC protocol error.
                    content = f"ERROR: {exc}"
                    is_error = True
                return self._ok(
                    rid,
                    {"content": [{"type": "text", "text": content}], "isError": is_error},
                )
            return self._error(rid, -32601, f"method not found: {method}")
        except Exception as exc:  # noqa: BLE001 - JSON-RPC error envelope
            return self._error(rid, -32603, f"internal error: {exc}")

    # ------------------------------------------------------------------
    # Filesystem handlers
    # ------------------------------------------------------------------

    def _safe(self, name: str) -> Path:
        assert self.root is not None
        candidate = (self.root / (name or ".")).resolve()
        try:
            candidate.relative_to(self.root)
        except ValueError as exc:
            raise PermissionError(f"path '{name}' resolves outside the server root") from exc
        return candidate

    def _list_dir(self, args: dict[str, Any]) -> str:
        target = self._safe(args.get("path", "."))
        if not target.is_dir():
            return f"ERROR: not a directory: {args.get('path')}"
        return "\n".join(sorted(p.name for p in target.iterdir()))

    def _read_text(self, args: dict[str, Any]) -> str:
        path = args.get("path", "")
        target = self._safe(path)
        if not target.is_file():
            return f"ERROR: not a file: {path}"
        try:
            return target.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            return f"ERROR: {exc}"

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _tool_to_dict(t: _ToolDef) -> dict[str, Any]:
        return {"name": t.name, "description": t.description, "inputSchema": t.input_schema}

    @staticmethod
    def _ok(rid: Any, result: Any) -> dict[str, Any]:
        return {"jsonrpc": JSONRPC_VERSION, "id": rid, "result": result}

    @staticmethod
    def _error(rid: Any, code: int, message: str) -> dict[str, Any]:
        return {"jsonrpc": JSONRPC_VERSION, "id": rid, "error": {"code": code, "message": message}}


def render_jsonrpc(envelope: dict[str, Any]) -> str:
    """Render an envelope as canonical JSON. Used for transport tests/logs."""
    return json.dumps(envelope, separators=(",", ":"), sort_keys=True)
