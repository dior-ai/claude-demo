"""Real-browser implementation of ``Browser`` — gated headless Chromium.

Implements the same surface as ``FakeBrowser`` but drives a real
Chromium via Playwright. Two enforcement points fire below this class:

  * **Route interceptor.** Every navigation, sub-resource fetch, and
    form submit the page issues hits ``page.route("**/*", ...)``. The
    handler calls ``BrowserProxy.allow_url`` and either continues or
    aborts the request. Off-allowlist hosts are aborted before a
    single byte leaves the browser.
  * **Secret substitution.** Form-fill values pass through
    ``BrowserProxy.substitute`` so ``${SECRET_NAME}`` placeholders are
    resolved at the egress boundary — the agent never holds the real
    value, exactly as in the FakeBrowser path.

Optional dep: install with ``pip install -e ".[browser]"`` and run
``playwright install chromium`` once. The module imports lazily so
that hosts without the extra can still ``import claude_demo`` without
trouble.

Same Browser Protocol → swapping engines is one constructor call;
the policy / proxy / audit / leak-check story is unchanged.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from ..proxy.browser import BrowserProxy, BrowserRequestKind

if TYPE_CHECKING:  # pragma: no cover - typing only
    from playwright.sync_api import Request, Route


class PlaywrightBrowser:
    """Chromium-backed implementation of the ``Browser`` Protocol.

    Use as a context manager so the Playwright handle, browser, and
    page are torn down cleanly even on errors.

    Construction parameters:

      proxy
          The ``BrowserProxy`` whose allowlist gates every request and
          whose secrets table backs the substitution in ``fill``.
      screenshot_dir
          Directory the ``screenshot`` op may write into. Path
          traversal outside this directory is rejected.
      host_resolver_rules
          Optional Chromium ``--host-resolver-rules`` entries. Lets a
          demo keep logical hostnames (``shop.local``) in URLs while
          pointing them at a localhost test server, mirroring the
          ``host_overrides`` facility of the credential proxy.
      headless
          Default True. Set False to watch the browser drive itself
          during a screen-record.
    """

    def __init__(
        self,
        proxy: BrowserProxy,
        *,
        screenshot_dir: Path | None = None,
        host_resolver_rules: list[str] | None = None,
        headless: bool = True,
        slow_mo_ms: int = 0,
    ) -> None:
        self.proxy = proxy
        self.screenshot_dir = screenshot_dir
        self.host_resolver_rules = list(host_resolver_rules or [])
        self.headless = headless
        # Pause inserted by Playwright between every action — only
        # useful in headed mode so the operator can actually see the
        # browser drive itself between ops.
        self.slow_mo_ms = slow_mo_ms

        self._pw = None
        self._browser = None
        self._context = None
        self._page = None
        self._current_url = ""

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start(self) -> "PlaywrightBrowser":
        try:
            from playwright.sync_api import sync_playwright
        except ModuleNotFoundError as exc:  # pragma: no cover - import guard
            raise RuntimeError(
                "playwright is not installed. Run: "
                "pip install -e \".[browser]\" && playwright install chromium"
            ) from exc
        except ImportError as exc:  # pragma: no cover - native-dep load failure
            # Surfaces the real cause (e.g., a Windows VC++ runtime DLL
            # missing for greenlet) rather than masking it as "not
            # installed". The operator needs to fix their host.
            raise RuntimeError(
                f"playwright import failed (likely a missing system "
                f"library on this host): {exc}"
            ) from exc

        args: list[str] = []
        if self.host_resolver_rules:
            args.append(
                "--host-resolver-rules=" + ",".join(self.host_resolver_rules)
            )

        self._pw = sync_playwright().start()
        self._browser = self._pw.chromium.launch(
            headless=self.headless,
            args=args,
            slow_mo=self.slow_mo_ms,
        )
        self._context = self._browser.new_context()
        self._page = self._context.new_page()
        # Single route handler for every request the page or its
        # sub-frames issue. Top-level navigation, XHR, images, iframe
        # documents, form submits — all funnel through here.
        self._page.route("**/*", self._on_route)
        return self

    def close(self) -> None:
        try:
            if self._page is not None:
                self._page.close()
        except Exception:  # noqa: BLE001 - cleanup is best-effort
            pass
        try:
            if self._context is not None:
                self._context.close()
        except Exception:  # noqa: BLE001
            pass
        try:
            if self._browser is not None:
                self._browser.close()
        except Exception:  # noqa: BLE001
            pass
        try:
            if self._pw is not None:
                self._pw.stop()
        except Exception:  # noqa: BLE001
            pass
        self._page = None
        self._context = None
        self._browser = None
        self._pw = None

    def __enter__(self) -> "PlaywrightBrowser":
        return self.start()

    def __exit__(self, *_exc: object) -> None:
        self.close()

    # ------------------------------------------------------------------
    # Browser Protocol
    # ------------------------------------------------------------------

    @property
    def current_url(self) -> str:
        return self._current_url

    def goto(self, url: str) -> str:
        self._require_started()
        try:
            response = self._page.goto(url)  # type: ignore[union-attr]
        except Exception as exc:  # noqa: BLE001 - Playwright surfaces a wide error type
            # The route interceptor likely aborted because the host is
            # off-allowlist. Surface the proxy's audit reason rather
            # than the opaque ``net::ERR_ABORTED`` string.
            blocked = self._last_blocked_for(url)
            if blocked is not None:
                return f"ERROR: {blocked}"
            return f"ERROR: navigation failed: {exc}"

        self._current_url = url
        status = response.status if response is not None else 0
        try:
            title = self._page.title()  # type: ignore[union-attr]
        except Exception:  # noqa: BLE001
            title = ""
        return f"OK status={status} url={url} title={title!r}"

    def fill(self, selector: str, value: str) -> str:
        self._require_started()
        substituted, used = self.proxy.substitute(value)
        try:
            self._page.fill(selector, substituted)  # type: ignore[union-attr]
        except Exception as exc:  # noqa: BLE001
            return f"ERROR: fill failed: {exc}"
        used_msg = (
            f" secrets_substituted=[{','.join(used)}]" if used else ""
        )
        return f"OK filled selector={selector!r}{used_msg}"

    def click(self, selector: str) -> str:
        self._require_started()
        try:
            self._page.click(selector)  # type: ignore[union-attr]
        except Exception as exc:  # noqa: BLE001
            return f"ERROR: click failed: {exc}"
        return f"OK clicked selector={selector!r}"

    def extract(self, selector: str) -> str:
        self._require_started()
        try:
            text = self._page.text_content(selector)  # type: ignore[union-attr]
        except Exception as exc:  # noqa: BLE001
            return f"ERROR: extract failed: {exc}"
        if text is None:
            return f"ERROR: no element matches selector {selector!r}"
        return text.strip()

    def screenshot(self, path: str) -> str:
        self._require_started()
        if self.screenshot_dir is None:
            return "ERROR: screenshot_dir not configured"
        target_dir = self.screenshot_dir.resolve()
        candidate = (target_dir / path).resolve()
        try:
            candidate.relative_to(target_dir)
        except ValueError:
            return f"ERROR: path '{path}' resolves outside the screenshot directory"
        target_dir.mkdir(parents=True, exist_ok=True)
        try:
            self._page.screenshot(path=str(candidate))  # type: ignore[union-attr]
        except Exception as exc:  # noqa: BLE001
            return f"ERROR: screenshot failed: {exc}"
        return f"OK screenshot={candidate.name} bytes={candidate.stat().st_size}"

    # ------------------------------------------------------------------
    # Route handler — every request the page issues lands here first
    # ------------------------------------------------------------------

    def _on_route(self, route: "Route", request: "Request") -> None:
        kind = self._classify_kind(request)
        result = self.proxy.allow_url(request.url, kind=kind)
        if not result.allowed:
            try:
                route.abort()
            except Exception:  # noqa: BLE001 - already aborted etc.
                pass
            return
        try:
            route.continue_()
        except Exception:  # noqa: BLE001
            pass

    @staticmethod
    def _classify_kind(request: "Request") -> BrowserRequestKind:
        rt = request.resource_type
        if rt == "document":
            # Top-level POST = form submit; top-level GET = navigate;
            # iframe document loads also land here as ``document`` —
            # treat them as sub-resources from a parent's perspective.
            method = (request.method or "GET").upper()
            if method in ("POST", "PUT", "PATCH", "DELETE"):
                return "submit"
            return "navigate"
        return "subresource"

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _require_started(self) -> None:
        if self._page is None:
            raise RuntimeError("PlaywrightBrowser not started; use as a context manager")

    def _last_blocked_for(self, url: str) -> str | None:
        """Most recent block reason recorded against ``url`` if any."""
        for entry in reversed(self.proxy.audit_log):
            if entry.url == url and entry.blocked:
                return entry.block_reason
        return None
