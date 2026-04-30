"""Browser surface the ``browser_tool`` calls into.

A small, stable interface so the tool itself doesn't depend on
Playwright. Two implementations ship today:

  * ``FakeBrowser``  (in ``_fake_browser.py``)  — deterministic, no
    install. Used by unit tests, the scripted demo, and the red-team
    suite.
  * a Playwright-backed implementation can be plugged in via the
    optional ``browser`` extra without touching the tool wiring.

Each method returns a short, plain-text status. ``ERROR:`` prefix
signals a tool-level failure to the runner (matches the convention
used across the other tools).
"""

from __future__ import annotations

from typing import Protocol


class Browser(Protocol):
    """Minimal browser surface. Five ops + a tiny inspector."""

    def goto(self, url: str) -> str: ...

    def fill(self, selector: str, value: str) -> str: ...

    def click(self, selector: str) -> str: ...

    def extract(self, selector: str) -> str: ...

    def screenshot(self, path: str) -> str: ...

    @property
    def current_url(self) -> str: ...
