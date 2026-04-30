"""In-process browser used by tests, the scripted demo, and red-team.

Zero install: no Chromium, no Node, no subprocess. Models a tiny
static site as a dict of ``FakePage`` fixtures. Navigation,
sub-resource loads, and form submits all route through the supplied
``BrowserProxy`` so the same allowlist gate fires that a real browser
would hit. The point isn't to be a browser; it's to exercise every
gate the substrate imposes on browser-shaped traffic.

A page can declare:

  * ``text``        — selector → visible text (for ``extract``)
  * ``inputs``      — selectors that accept ``fill``
  * ``form_action`` — URL the next ``click`` (on a submit selector)
                      will POST to, with the filled values
  * ``auto_loads``  — sub-resource URLs the page fires on navigate
                      (XHR / images / iframes — modelled together)

The screenshot op writes a 1-byte placeholder; the goal is just to
verify the path-traversal guard, not to render anything.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from ..proxy.browser import BrowserProxy


@dataclass
class FakePage:
    """One static fixture page."""

    title: str = ""
    text: dict[str, str] = field(default_factory=dict)
    inputs: set[str] = field(default_factory=set)
    form_action: str | None = None
    auto_loads: tuple[str, ...] = ()


class NotNavigatedError(Exception):
    """Raised when an op needs a current page but none has been loaded."""


@dataclass
class FakeBrowser:
    """In-process Browser implementation backed by a fixture dict.

    Every network-shaped action consults ``proxy.allow_url`` first.
    Form-fill values pass through ``proxy.substitute`` so secret
    placeholders are resolved at the egress boundary, never inside
    the agent context. The ``filled`` map is the only mutable state
    a page accumulates between calls.
    """

    proxy: BrowserProxy
    pages: dict[str, FakePage] = field(default_factory=dict)
    screenshot_dir: Path | None = None

    _current_url: str = ""
    _filled: dict[str, str] = field(default_factory=dict)
    _filled_secret_names: list[str] = field(default_factory=list)

    @property
    def current_url(self) -> str:
        return self._current_url

    # ------------------------------------------------------------------
    # Ops
    # ------------------------------------------------------------------

    def goto(self, url: str) -> str:
        nav = self.proxy.allow_url(url, kind="navigate")
        if not nav.allowed:
            return f"ERROR: {nav.block_reason}"

        self._current_url = url
        # Filled state is per-page; navigating away clears it.
        self._filled.clear()
        self._filled_secret_names.clear()

        page = self.pages.get(url)
        if page is None:
            # Logical 404: the navigation was allowed (host on allowlist)
            # but we have no fixture for this URL. Treat as empty page.
            return f"OK status=404 url={url}"

        # Fire auto-loaded sub-resources. Each goes through the same
        # gate; blocked ones are recorded but the page itself loads.
        blocked = 0
        for sub_url in page.auto_loads:
            r = self.proxy.allow_url(sub_url, kind="subresource")
            if not r.allowed:
                blocked += 1

        suffix = f" sub_blocked={blocked}" if blocked else ""
        return f"OK status=200 url={url} title={page.title!r}{suffix}"

    def fill(self, selector: str, value: str) -> str:
        page = self._require_page()
        if selector not in page.inputs:
            return f"ERROR: selector '{selector}' is not a fillable input on this page"
        substituted, used = self.proxy.substitute(value)
        self._filled[selector] = substituted
        for name in used:
            if name not in self._filled_secret_names:
                self._filled_secret_names.append(name)
        used_msg = (
            f" secrets_substituted=[{','.join(used)}]" if used else ""
        )
        return f"OK filled selector={selector!r}{used_msg}"

    def click(self, selector: str) -> str:
        page = self._require_page()

        # Submit selector: fire the form action through the proxy.
        if selector in {"#submit", "button[type=submit]"} and page.form_action:
            r = self.proxy.allow_url(page.form_action, kind="submit")
            if not r.allowed:
                return f"ERROR: {r.block_reason}"
            sent = list(self._filled.keys())
            secret_names = list(self._filled_secret_names)
            self._filled.clear()
            self._filled_secret_names.clear()
            secret_msg = (
                f" secrets_sent=[{','.join(secret_names)}]"
                if secret_names
                else ""
            )
            return (
                f"OK submitted action={page.form_action} fields={sent}{secret_msg}"
            )

        return f"OK clicked selector={selector!r}"

    def extract(self, selector: str) -> str:
        page = self._require_page()
        if selector not in page.text:
            return f"ERROR: no element matches selector {selector!r}"
        return page.text[selector]

    def screenshot(self, path: str) -> str:
        if self.screenshot_dir is None:
            return "ERROR: screenshot_dir not configured"
        target_dir = self.screenshot_dir.resolve()
        candidate = (target_dir / path).resolve()
        try:
            candidate.relative_to(target_dir)
        except ValueError:
            return f"ERROR: path '{path}' resolves outside the screenshot directory"
        target_dir.mkdir(parents=True, exist_ok=True)
        candidate.write_bytes(b"\x89PNG\r\n\x1a\n")  # tiny placeholder
        return f"OK screenshot={candidate.name} bytes={candidate.stat().st_size}"

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _require_page(self) -> FakePage:
        if not self._current_url:
            raise NotNavigatedError("no page loaded; call goto first")
        page = self.pages.get(self._current_url)
        if page is None:
            raise NotNavigatedError(f"no fixture for {self._current_url}")
        return page
