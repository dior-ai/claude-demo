"""Tiny in-process HTTP mock backend for the credential-safety demo.

Serves two endpoints under any Host header (the proxy adds Host so the
server can distinguish, but we don't actually branch on it for the demo):

  GET  /widgets    -> JSON list, requires "Authorization: Bearer <expected>"
  POST /report     -> echoes the body back, requires the same header

Returns 401 when the auth header is missing, wrong, or still has a
``${...}`` placeholder in it (which is how we prove the proxy DID
substitute the real secret on legitimate calls).

Spin up with ``with mock_backend(port, expected_token):`` — context
manager handles thread + shutdown.
"""

from __future__ import annotations

import contextlib
import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer


def _make_handler(expected_token: str):
    class Handler(BaseHTTPRequestHandler):
        # Quiet by default — the demo's own logging is what we want to see.
        def log_message(self, fmt: str, *args) -> None:  # noqa: A003
            return

        def _check_auth(self) -> bool:
            auth = self.headers.get("Authorization", "")
            if not auth.startswith("Bearer "):
                return False
            token = auth[len("Bearer "):].strip()
            if "${" in token:
                # Placeholder leaked through unsubstituted — definitely not auth.
                return False
            return token == expected_token

        def _send_json(self, status: int, payload: dict) -> None:
            body = json.dumps(payload).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:  # noqa: N802
            if self.path == "/widgets":
                if not self._check_auth():
                    self._send_json(401, {"error": "missing or invalid Bearer token"})
                    return
                self._send_json(
                    200,
                    {
                        "widgets": [
                            {"id": 1, "category": "alpha", "value": 12},
                            {"id": 2, "category": "beta", "value": 7},
                            {"id": 3, "category": "alpha", "value": 9},
                        ]
                    },
                )
                return
            self._send_json(404, {"error": f"no route for GET {self.path}"})

        def do_POST(self) -> None:  # noqa: N802
            if self.path == "/report":
                if not self._check_auth():
                    self._send_json(401, {"error": "missing or invalid Bearer token"})
                    return
                length = int(self.headers.get("Content-Length", "0") or "0")
                body = self.rfile.read(length).decode("utf-8") if length else ""
                self._send_json(200, {"received": body})
                return
            self._send_json(404, {"error": f"no route for POST {self.path}"})

    return Handler


@contextlib.contextmanager
def mock_backend(host: str, port: int, expected_token: str):
    """Run an HTTPServer in a background thread for the duration of the with-block."""
    server = HTTPServer((host, port), _make_handler(expected_token))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
