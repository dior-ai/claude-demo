"""HTTP tool that always routes through the credential proxy.

The agent (LLM-driven or scripted) only sees this tool — it never has a
direct urllib / requests handle. That keeps the proxy the single egress
chokepoint: every outbound HTTP must pass through the allowlist check
and secret substitution, no shortcuts.

Tool input shape:

  url:     str   full URL (scheme + host required)
  method:  str   "GET" | "POST" (default "GET")
  headers: dict  optional. Values may contain ${SECRET_NAME} placeholders.
  body:    str   optional. May contain ${SECRET_NAME} placeholders.

The tool result is a string. On success it begins with "[status=...]"
and includes the names of any secrets the proxy substituted (names only,
never values). On block it begins with "ERROR:" so PostToolUse hooks and
the run state both classify it as a failed call.
"""

from __future__ import annotations

from typing import Any

from ..proxy.credential import CredentialProxy
from .base import Tool


def make_http_tool(proxy: CredentialProxy) -> Tool:
    def run(tool_input: dict[str, Any]) -> str:
        url = tool_input.get("url", "")
        if not isinstance(url, str) or not url:
            return "ERROR: 'url' is required and must be a string"

        method = str(tool_input.get("method", "GET")).upper()
        if method not in ("GET", "POST"):
            return f"ERROR: unsupported method '{method}'. Use GET or POST."

        headers = tool_input.get("headers") or {}
        if not isinstance(headers, dict):
            return "ERROR: 'headers' must be an object"

        body = tool_input.get("body")
        if body is not None and not isinstance(body, str):
            return "ERROR: 'body' must be a string"

        result = proxy.request(method=method, url=url, headers=headers, body=body)

        if result.blocked:
            return f"ERROR: {result.block_reason}"

        used = (
            f" secrets_substituted=[{','.join(result.substitutions)}]"
            if result.substitutions
            else ""
        )
        body_preview = result.body
        if len(body_preview) > 2000:
            body_preview = body_preview[:2000] + "\n...[truncated]"
        return f"[status={result.status}{used}]\n{body_preview}"

    return Tool(
        name="http_request",
        description=(
            "Make an HTTP request via the credential proxy.\n\n"
            "AUTH IS HANDLED FOR YOU. When a request needs a secret "
            "(API key, bearer token, etc.), pass a placeholder of the "
            "EXACT form ${SECRET_NAME} — with literal dollar sign and "
            "curly braces — anywhere in headers or the request body. "
            "The proxy substitutes the real value at send time, but "
            "ONLY if the target host is on the allowlist.\n\n"
            "DO NOT try to resolve the placeholder yourself. DO NOT "
            "guess the secret. DO NOT use 'YOUR_TOKEN_HERE' or any "
            "other stand-in. Pass the literal placeholder verbatim — "
            "for example, 'Authorization: Bearer ${WIDGETS_TOKEN}' is "
            "exactly the string to send. The platform handles the "
            "rest. Off-allowlist hosts are refused before any secret "
            "is read, so no secret can leak via a bad target."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "method": {
                    "type": "string",
                    "enum": ["GET", "POST"],
                    "description": "HTTP method.",
                },
                "url": {
                    "type": "string",
                    "description": "Full URL including scheme and host.",
                },
                "headers": {
                    "type": "object",
                    "description": "Headers to send. Values may contain ${SECRET_NAME} placeholders.",
                    "additionalProperties": {"type": "string"},
                },
                "body": {
                    "type": "string",
                    "description": "Optional request body. May contain ${SECRET_NAME} placeholders.",
                },
            },
            "required": ["url"],
        },
        run=run,
    )
