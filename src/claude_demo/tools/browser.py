"""``browser_tool`` — gated, op-dispatched browser automation.

Five operations: ``goto``, ``fill``, ``click``, ``extract``,
``screenshot``. Each call goes through the same hook chain as every
other tool, so the policy / audit / leak-check story is unchanged.
The browser itself is parametrised on a ``Browser`` Protocol so the
tool runs against an in-process ``FakeBrowser`` (deterministic, no
install) or a real Playwright-backed browser (optional, behind the
``browser`` extra).

Two enforcement points fire below this tool:

  1. **Policy hook** — denies whole ops (``gov-airgapped`` denies
     ``fill`` and ``click``) and refuses sensitive selectors via
     ``policy.browser_forbidden_selectors``.
  2. **BrowserProxy** — refuses any URL the browser is about to
     navigate to, sub-resource it would auto-load, or form-submit
     URL it would POST to. Reuses ``policy.http_allowlist`` so the
     same source of truth gates both ``http_tool`` and the browser.

Form-fill values support ``${SECRET_NAME}`` placeholders; the proxy
substitutes them at fill time so the agent never holds the real
value, mirroring the ``http_tool`` design.

Tool input shape:

  op:        "goto" | "fill" | "click" | "extract" | "screenshot"
  url:       str  (required for goto)
  selector:  str  (required for fill / click / extract)
  value:     str  (required for fill; may contain ${SECRET})
  path:      str  (required for screenshot; relative to screenshot dir)
"""

from __future__ import annotations

from typing import Any

from ._browser_iface import Browser
from .base import Tool


def make_browser_tool(browser: Browser) -> Tool:
    def run(tool_input: dict[str, Any]) -> str:
        op = tool_input.get("op")
        if not isinstance(op, str) or not op:
            return "ERROR: 'op' is required (goto|fill|click|extract|screenshot)"

        if op == "goto":
            url = tool_input.get("url", "")
            if not isinstance(url, str) or not url:
                return "ERROR: 'url' is required for op=goto"
            return browser.goto(url)

        if op == "fill":
            selector = tool_input.get("selector", "")
            value = tool_input.get("value", "")
            if not isinstance(selector, str) or not selector:
                return "ERROR: 'selector' is required for op=fill"
            if not isinstance(value, str):
                return "ERROR: 'value' must be a string for op=fill"
            return browser.fill(selector, value)

        if op == "click":
            selector = tool_input.get("selector", "")
            if not isinstance(selector, str) or not selector:
                return "ERROR: 'selector' is required for op=click"
            return browser.click(selector)

        if op == "extract":
            selector = tool_input.get("selector", "")
            if not isinstance(selector, str) or not selector:
                return "ERROR: 'selector' is required for op=extract"
            return browser.extract(selector)

        if op == "screenshot":
            path = tool_input.get("path", "")
            if not isinstance(path, str) or not path:
                return "ERROR: 'path' is required for op=screenshot"
            return browser.screenshot(path)

        return (
            f"ERROR: unknown op '{op}'. "
            "Valid ops: goto, fill, click, extract, screenshot."
        )

    return Tool(
        name="browser_tool",
        description=(
            "Drive a gated browser. Operations: goto (navigate), fill "
            "(text inputs; values may contain ${SECRET_NAME} placeholders, "
            "substituted at the egress boundary), click (selectors and "
            "submit buttons), extract (return visible text by selector), "
            "screenshot (save to a relative path under the screenshot "
            "directory). Every navigation, sub-resource, and form submit "
            "is checked against the policy's HTTP allowlist; sensitive "
            "selectors (e.g., credit-card fields) may be additionally "
            "blocked by the policy."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "op": {
                    "type": "string",
                    "enum": ["goto", "fill", "click", "extract", "screenshot"],
                    "description": "Operation to perform.",
                },
                "url": {
                    "type": "string",
                    "description": "Full URL for op=goto.",
                },
                "selector": {
                    "type": "string",
                    "description": "CSS-style selector for op=fill/click/extract.",
                },
                "value": {
                    "type": "string",
                    "description": (
                        "Value to fill (op=fill). May contain "
                        "${SECRET_NAME} placeholders."
                    ),
                },
                "path": {
                    "type": "string",
                    "description": "Relative path under the screenshot directory (op=screenshot).",
                },
            },
            "required": ["op"],
        },
        run=run,
    )
