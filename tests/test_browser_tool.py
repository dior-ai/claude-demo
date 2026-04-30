"""Tests for the browser-tool layer.

Three planes are exercised:

  * ``BrowserProxy``    — allowlist gate + secret substitution
  * ``FakeBrowser``     — in-process implementation of the Browser interface
  * ``browser_tool``    — op dispatch + error shape

The integration through the policy hook is covered by
``test_policy.py`` and the end-to-end leak / layer assertions by
``test_redteam.py``. These tests stay close to each unit.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from claude_demo.proxy.browser import BrowserProxy
from claude_demo.tools import FakeBrowser, FakePage, make_browser_tool


def _make_browser(
    *,
    allowed: set[str] | None = None,
    secrets: dict[str, str] | None = None,
    pages: dict[str, FakePage] | None = None,
    screenshot_dir: Path | None = None,
) -> tuple[FakeBrowser, BrowserProxy]:
    proxy = BrowserProxy(
        allowed_hosts=allowed or {"shop.local"},
        secrets=secrets or {},
    )
    browser = FakeBrowser(
        proxy=proxy,
        pages=pages or {},
        screenshot_dir=screenshot_dir,
    )
    return browser, proxy


class TestBrowserProxy(unittest.TestCase):
    def test_allows_url_on_allowlist(self) -> None:
        proxy = BrowserProxy(allowed_hosts={"shop.local"})
        result = proxy.allow_url("http://shop.local/x", kind="navigate")
        self.assertTrue(result.allowed)
        self.assertEqual(len(proxy.audit_log), 1)
        self.assertFalse(proxy.audit_log[0].blocked)

    def test_blocks_off_allowlist(self) -> None:
        proxy = BrowserProxy(allowed_hosts={"shop.local"})
        result = proxy.allow_url("http://evil.local/x", kind="navigate")
        self.assertFalse(result.allowed)
        self.assertIn("evil.local", result.block_reason)
        self.assertTrue(proxy.audit_log[0].blocked)

    def test_substitute_known_placeholder(self) -> None:
        proxy = BrowserProxy(allowed_hosts=set(), secrets={"FOO": "real-foo"})
        out, used = proxy.substitute("hi ${FOO} bye")
        self.assertEqual(out, "hi real-foo bye")
        self.assertEqual(used, ["FOO"])

    def test_substitute_unknown_placeholder_left_in_place(self) -> None:
        proxy = BrowserProxy(allowed_hosts=set(), secrets={})
        out, used = proxy.substitute("hi ${UNKNOWN} bye")
        # Unknown placeholders are passed through verbatim; consumers
        # see a literal ${UNKNOWN} which fails loudly downstream.
        self.assertIn("${UNKNOWN}", out)
        self.assertEqual(used, [])


class TestFakeBrowserNavigation(unittest.TestCase):
    def test_goto_allowlisted_loads_page(self) -> None:
        page = FakePage(title="hello", text={".body": "world"})
        browser, _ = _make_browser(pages={"http://shop.local/p": page})
        out = browser.goto("http://shop.local/p")
        self.assertIn("OK", out)
        self.assertIn("'hello'", out)
        self.assertEqual(browser.current_url, "http://shop.local/p")

    def test_goto_off_allowlist_blocked(self) -> None:
        browser, _ = _make_browser()
        out = browser.goto("http://evil.local/x")
        self.assertTrue(out.startswith("ERROR:"))
        self.assertIn("not on allowlist", out)
        self.assertEqual(browser.current_url, "")

    def test_goto_404_for_unknown_url_on_allowlist(self) -> None:
        browser, _ = _make_browser()
        out = browser.goto("http://shop.local/no-such-page")
        self.assertIn("status=404", out)

    def test_subresources_routed_through_proxy(self) -> None:
        page = FakePage(
            title="injected",
            auto_loads=("http://evil.local/exfil.png",),
        )
        browser, proxy = _make_browser(pages={"http://shop.local/p": page})
        out = browser.goto("http://shop.local/p")
        # Page itself loads; subresource is blocked.
        self.assertIn("OK", out)
        self.assertIn("sub_blocked=1", out)
        blocked = [e for e in proxy.audit_log if e.blocked]
        self.assertEqual(len(blocked), 1)
        self.assertEqual(blocked[0].kind, "subresource")


class TestFakeBrowserFillSubmit(unittest.TestCase):
    def setUp(self) -> None:
        self.page = FakePage(
            title="checkout",
            text={".total": "$10"},
            inputs={"#email", "#submit"},
            form_action="http://shop.local/submit",
        )
        self.browser, self.proxy = _make_browser(
            secrets={"USER_EMAIL": "real@example.com"},
            pages={"http://shop.local/checkout": self.page},
        )
        self.browser.goto("http://shop.local/checkout")

    def test_fill_substitutes_secret(self) -> None:
        out = self.browser.fill("#email", "${USER_EMAIL}")
        self.assertIn("OK", out)
        self.assertIn("USER_EMAIL", out)
        # Real value must NOT appear in the tool result string.
        self.assertNotIn("real@example.com", out)

    def test_fill_unknown_selector_errors(self) -> None:
        out = self.browser.fill("#nope", "x")
        self.assertTrue(out.startswith("ERROR:"))

    def test_click_submit_routes_through_proxy(self) -> None:
        self.browser.fill("#email", "${USER_EMAIL}")
        out = self.browser.click("#submit")
        self.assertIn("OK submitted", out)
        # Submit URL was checked AND it doesn't contain the real email.
        submit_audit = [e for e in self.proxy.audit_log if e.kind == "submit"]
        self.assertEqual(len(submit_audit), 1)
        self.assertNotIn("real@example.com", submit_audit[0].url)

    def test_click_submit_off_allowlist_blocked(self) -> None:
        # Reconfigure: form_action on a blocked host.
        self.page.form_action = "http://evil.local/steal"
        out = self.browser.click("#submit")
        self.assertTrue(out.startswith("ERROR:"))
        self.assertIn("not on allowlist", out)


class TestFakeBrowserScreenshot(unittest.TestCase):
    def test_screenshot_writes_inside_dir(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            shot_dir = Path(tmp)
            page = FakePage(title="x")
            browser, _ = _make_browser(
                pages={"http://shop.local/x": page},
                screenshot_dir=shot_dir,
            )
            browser.goto("http://shop.local/x")
            out = browser.screenshot("a.png")
            self.assertIn("OK", out)
            self.assertTrue((shot_dir / "a.png").exists())

    def test_screenshot_path_traversal_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            shot_dir = Path(tmp)
            browser, _ = _make_browser(screenshot_dir=shot_dir)
            out = browser.screenshot("../escape.png")
            self.assertTrue(out.startswith("ERROR:"))
            self.assertIn("outside", out)


class TestBrowserToolDispatch(unittest.TestCase):
    """Drive the tool via dict input — the same shape the runner sees."""

    def setUp(self) -> None:
        page = FakePage(
            title="t",
            text={".price": "$1"},
            inputs={"#email"},
        )
        self.browser, _ = _make_browser(
            pages={"http://shop.local/p": page},
            secrets={"USER_EMAIL": "real@example.com"},
        )
        self.tool = make_browser_tool(self.browser)

    def test_goto_dispatch(self) -> None:
        out = self.tool.run({"op": "goto", "url": "http://shop.local/p"})
        self.assertIn("OK", out)

    def test_extract_dispatch(self) -> None:
        self.tool.run({"op": "goto", "url": "http://shop.local/p"})
        out = self.tool.run({"op": "extract", "selector": ".price"})
        self.assertEqual(out, "$1")

    def test_fill_dispatch(self) -> None:
        self.tool.run({"op": "goto", "url": "http://shop.local/p"})
        out = self.tool.run(
            {"op": "fill", "selector": "#email", "value": "${USER_EMAIL}"}
        )
        self.assertIn("OK", out)
        self.assertNotIn("real@example.com", out)

    def test_unknown_op_errors(self) -> None:
        out = self.tool.run({"op": "execute_script", "code": "x"})
        self.assertTrue(out.startswith("ERROR:"))

    def test_missing_op_errors(self) -> None:
        out = self.tool.run({})
        self.assertTrue(out.startswith("ERROR:"))

    def test_goto_missing_url_errors(self) -> None:
        out = self.tool.run({"op": "goto"})
        self.assertTrue(out.startswith("ERROR:"))

    def test_fill_requires_selector(self) -> None:
        self.tool.run({"op": "goto", "url": "http://shop.local/p"})
        out = self.tool.run({"op": "fill", "value": "x"})
        self.assertTrue(out.startswith("ERROR:"))


if __name__ == "__main__":
    unittest.main()
