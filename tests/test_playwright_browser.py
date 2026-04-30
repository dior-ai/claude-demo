"""Tests for the optional Playwright-backed browser implementation.

The unit tests here have two tiers:

  * **Always-on shape checks.** The class is importable, conforms to
    the ``Browser`` Protocol, and ``start()`` raises a clear,
    actionable error when the ``[browser]`` extra isn't installed.

  * **Headless smoke test.** Requires both ``playwright`` and a
    Chromium binary (``playwright install chromium``). Drives a real
    browser through the static-site server and asserts the
    allowlist gate fires for both an allowed navigation and an
    off-allowlist exfiltration attempt.

The smoke test is skipped automatically on hosts without the extra,
so the headline ``unittest discover tests`` run stays green out of
the box.
"""

from __future__ import annotations

import importlib.util
import tempfile
import unittest
from pathlib import Path

from claude_demo.proxy.browser import BrowserProxy
from claude_demo.tools._browser_iface import Browser


def _playwright_available() -> bool:
    """Real availability check — package present AND its native deps load.

    A bare ``find_spec`` only proves the package directory exists. On
    Windows hosts missing the VC++ Redistributable, ``import
    playwright.sync_api`` still fails at runtime because greenlet's
    compiled extension can't load. Attempt the full import here so a
    misconfigured host skips the smoke tests instead of failing them.
    """
    if importlib.util.find_spec("playwright") is None:
        return False
    try:
        import playwright.sync_api  # noqa: F401  - import probe
    except Exception:  # noqa: BLE001 - any load error is "not available"
        return False
    return True


HAS_PLAYWRIGHT = _playwright_available()


class TestPlaywrightBrowserShape(unittest.TestCase):
    """Always-on tests: class is importable and conforms to the Protocol."""

    def test_class_imports(self) -> None:
        # The lazy __getattr__ on the tools package only triggers the
        # playwright import when the attribute is read. So the import
        # below works whether or not playwright is installed — it
        # imports the module, not the underlying SDK.
        from claude_demo.tools._playwright_browser import PlaywrightBrowser

        self.assertTrue(callable(PlaywrightBrowser))

    def test_satisfies_browser_protocol(self) -> None:
        from claude_demo.tools._playwright_browser import PlaywrightBrowser

        proxy = BrowserProxy(allowed_hosts=set())
        # Don't .start() — we just check method presence at the
        # instance level. ``Browser`` is a runtime-checkable Protocol
        # via duck-typing; assert each required attribute exists.
        instance: Browser = PlaywrightBrowser(proxy=proxy)
        for attr in ("goto", "fill", "click", "extract", "screenshot"):
            self.assertTrue(callable(getattr(instance, attr)))
        # current_url is a property; reading on a non-started instance
        # is allowed and returns "".
        self.assertEqual(instance.current_url, "")


@unittest.skipUnless(
    HAS_PLAYWRIGHT,
    "playwright not installed — install with `pip install -e \".[browser]\" "
    "&& playwright install chromium` to run the smoke test.",
)
class TestPlaywrightBrowserSmoke(unittest.TestCase):
    """Drives a real Chromium against the demo's static fixture site."""

    def setUp(self) -> None:
        # Local imports so the module imports cleanly without playwright.
        import socket

        from claude_demo.tools._playwright_browser import PlaywrightBrowser
        from examples.browser_research.static_site import static_site

        # Ask the OS for an ephemeral free port instead of hard-coding
        # one. Hard-coded ports fight with leftover zombies on Windows
        # when an earlier test run aborted before HTTPServer cleanup
        # ran; ephemeral allocation makes the test self-contained.
        self.host = "127.0.0.1"
        with socket.socket() as probe:
            probe.bind((self.host, 0))
            self.port = probe.getsockname()[1]
        self._site_cm = static_site(self.host, self.port)
        self._site_cm.__enter__()

        self.proxy = BrowserProxy(
            allowed_hosts={"shop.local"},
            secrets={"USER_EMAIL": "real@example.com"},
        )
        self._tmp = tempfile.TemporaryDirectory()
        self.shot_dir = Path(self._tmp.name)
        self.browser = PlaywrightBrowser(
            proxy=self.proxy,
            screenshot_dir=self.shot_dir,
            host_resolver_rules=[f"MAP shop.local {self.host}:{self.port}"],
            headless=True,
        ).start()

    def tearDown(self) -> None:
        try:
            self.browser.close()
        finally:
            try:
                self._site_cm.__exit__(None, None, None)
            finally:
                self._tmp.cleanup()

    def test_goto_allowlisted_loads_page(self) -> None:
        out = self.browser.goto("http://shop.local/products")
        self.assertIn("OK status=200", out)
        # Title comes from the served HTML.
        self.assertIn("Widgets", out)

    def test_goto_off_allowlist_blocked(self) -> None:
        out = self.browser.goto("http://evil.local/page")
        self.assertTrue(out.startswith("ERROR:"))
        self.assertIn("not on allowlist", out)
        # Audit log recorded the block.
        blocked = [e for e in self.proxy.audit_log if e.blocked]
        self.assertGreaterEqual(len(blocked), 1)
        self.assertEqual(blocked[-1].host, "evil.local")

    def test_extract_returns_visible_text(self) -> None:
        self.browser.goto("http://shop.local/products")
        out = self.browser.extract(".price")
        self.assertEqual(out, "$29.99")

    def test_fill_substitutes_and_does_not_leak_value(self) -> None:
        self.browser.goto("http://shop.local/checkout")
        out = self.browser.fill("#email", "${USER_EMAIL}")
        self.assertIn("OK", out)
        self.assertIn("USER_EMAIL", out)
        # Real value must NOT appear in the result string.
        self.assertNotIn("real@example.com", out)


if __name__ == "__main__":
    unittest.main()
