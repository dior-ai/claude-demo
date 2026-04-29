"""Tests for the MCP stub server, client, and tool adapter."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from claude_demo.mcp import MCPClient, StubMCPServer, wrap_as_tools
from claude_demo.mcp.client import MCPError


class TestStubServer(unittest.TestCase):
    def test_initialize_envelope(self) -> None:
        server = StubMCPServer(name="t")
        resp = server.handle({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})
        self.assertEqual(resp["jsonrpc"], "2.0")
        self.assertEqual(resp["id"], 1)
        self.assertIn("protocolVersion", resp["result"])
        self.assertEqual(resp["result"]["serverInfo"]["name"], "t")

    def test_tools_list_includes_echo(self) -> None:
        server = StubMCPServer(name="t")
        resp = server.handle({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
        names = [t["name"] for t in resp["result"]["tools"]]
        self.assertIn("echo", names)

    def test_tools_call_echo(self) -> None:
        server = StubMCPServer(name="t")
        resp = server.handle(
            {"jsonrpc": "2.0", "id": 3, "method": "tools/call",
             "params": {"name": "echo", "arguments": {"msg": "hello"}}}
        )
        self.assertFalse(resp["result"]["isError"])
        self.assertEqual(resp["result"]["content"][0]["text"], "echo:hello")

    def test_unknown_method_returns_error_envelope(self) -> None:
        server = StubMCPServer(name="t")
        resp = server.handle({"jsonrpc": "2.0", "id": 4, "method": "nonsense"})
        self.assertIn("error", resp)
        self.assertEqual(resp["error"]["code"], -32601)

    def test_unknown_tool_returns_error(self) -> None:
        server = StubMCPServer(name="t")
        resp = server.handle(
            {"jsonrpc": "2.0", "id": 5, "method": "tools/call",
             "params": {"name": "missing", "arguments": {}}}
        )
        self.assertIn("error", resp)


class TestStubFilesystemTools(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / "hello.txt").write_text("hi from mcp", encoding="utf-8")
        (self.root / "sub").mkdir()
        (self.root / "sub" / "nested.txt").write_text("nested", encoding="utf-8")
        self.server = StubMCPServer(name="fs", root=self.root)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_list_dir(self) -> None:
        client = MCPClient(self.server.handle, server_name="fs")
        out = client.call_tool("list_dir", {"path": "."})
        self.assertIn("hello.txt", out)
        self.assertIn("sub", out)

    def test_read_text(self) -> None:
        client = MCPClient(self.server.handle, server_name="fs")
        out = client.call_tool("read_text", {"path": "hello.txt"})
        self.assertIn("hi from mcp", out)

    def test_path_traversal_rejected(self) -> None:
        client = MCPClient(self.server.handle, server_name="fs")
        out = client.call_tool("read_text", {"path": "../etc/passwd"})
        self.assertTrue(out.startswith("ERROR:"))


class TestMCPClient(unittest.TestCase):
    def test_initialize_then_list_then_call(self) -> None:
        server = StubMCPServer(name="t")
        client = MCPClient(server.handle, server_name="t")
        client.initialize()
        tools = client.list_tools()
        self.assertGreater(len(tools), 0)
        result = client.call_tool("echo", {"msg": "x"})
        self.assertEqual(result, "echo:x")

    def test_lazy_initialize_on_call(self) -> None:
        # Calling list_tools without explicit initialize should still work.
        server = StubMCPServer(name="t")
        client = MCPClient(server.handle, server_name="t")
        tools = client.list_tools()
        self.assertGreater(len(tools), 0)

    def test_error_response_raises(self) -> None:
        server = StubMCPServer(name="t")
        client = MCPClient(server.handle, server_name="t")
        with self.assertRaises(MCPError):
            client.call_tool("does_not_exist", {})


class TestAdapter(unittest.TestCase):
    def test_wrap_as_tools_naming(self) -> None:
        server = StubMCPServer(name="testsrv")
        client = MCPClient(server.handle, server_name="testsrv")
        tools = wrap_as_tools(client)
        names = [t.name for t in tools]
        self.assertIn("mcp__testsrv__echo", names)

    def test_wrapped_tool_runs(self) -> None:
        server = StubMCPServer(name="srv")
        client = MCPClient(server.handle, server_name="srv")
        tools = wrap_as_tools(client)
        echo_tool = next(t for t in tools if t.name.endswith("__echo"))
        result = echo_tool.run({"msg": "wrapped"})
        self.assertEqual(result, "echo:wrapped")


if __name__ == "__main__":
    unittest.main()
