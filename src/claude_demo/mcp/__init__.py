"""Model Context Protocol — minimal client + in-process stub server.

The shape mirrors the wire MCP protocol (JSON-RPC 2.0, ``tools/list`` and
``tools/call`` methods, ``mcp__<server>__<tool>`` naming) so the integration
points are real even though the transport is in-process for the demo.
"""

from .adapter import wrap_as_tools
from .client import MCPClient
from .stub_server import StubMCPServer

__all__ = ["MCPClient", "StubMCPServer", "wrap_as_tools"]
