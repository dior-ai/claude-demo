"""Tool layer — concrete tools the runtime can dispatch."""

from typing import Any

from ._browser_iface import Browser
from ._fake_browser import FakeBrowser, FakePage
from .base import Tool
from .browser import make_browser_tool
from .code import make_code_runner
from .file import make_file_tool
from .http import make_http_tool

__all__ = [
    "Browser",
    "FakeBrowser",
    "FakePage",
    "PlaywrightBrowser",
    "Tool",
    "make_browser_tool",
    "make_code_runner",
    "make_file_tool",
    "make_http_tool",
]


def __getattr__(name: str) -> Any:
    """Lazy import for the optional Playwright-backed browser.

    Hosts without the ``[browser]`` extra installed can still
    ``from claude_demo.tools import ...`` for everything else; the
    Playwright dep is only loaded when a caller actually asks for it.
    """
    if name == "PlaywrightBrowser":
        from ._playwright_browser import PlaywrightBrowser

        return PlaywrightBrowser
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
