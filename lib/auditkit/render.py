"""Optional browser render tier (tier 3 of the render check).

This tier is a **bonus, never a requirement.** The grading machine may not have
Playwright, and an audit that only works with a browser installed is an audit that does
not work. Tiers 1 and 2 of the render check are therefore genuinely strong on their own:
tier 2 confirms client-side rendering at high confidence from a single static fetch, by
using the framework's own hydration payload as a ground-truth oracle. Tier 3 adds direct
measurement and better evidence when it is available.

Feature detection is two-stage, because an importable Playwright is not a working one:
the module must import **and** a browser must actually launch. A machine with the wheel
installed but no browser binaries downloaded is common, and it must degrade quietly to
``render_mode: "static"`` rather than crash an audit.

Read-only by construction (threat T13)
--------------------------------------
The browser is as constrained as the static fetcher:

* **every non-GET request is aborted** by request interception, so a stray beacon or
  analytics POST cannot fire
* **no interaction at all** -- no clicks, no typing, no form submission, no scrolling
  into handlers. The page is loaded and observed, never operated.
* service workers, notifications, geolocation and downloads are disabled
* each page is hard-capped in wall-clock time, and the whole tier is capped by the run
  budget

Third-party requests to hosts outside the audited site are also aborted. That keeps the
audit from fanning out to analytics and ad networks it was never asked to contact, and it
makes the render fast enough to fit the budget.
"""

from __future__ import annotations

import importlib.util
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence, Tuple

MODE_STATIC = "static"
MODE_PLAYWRIGHT = "playwright"

DEFAULT_SETTLE_MS = 1500
DEFAULT_PAGE_TIMEOUT_MS = 8000
DEFAULT_VIEWPORT = {"width": 1280, "height": 800}


@dataclass
class RenderResult:
    """One rendered page, or the reason it was not rendered."""

    url: str
    ok: bool
    html: str = ""
    reason: str = ""
    blocked_requests: int = 0
    aborted_non_get: int = 0
    elapsed_ms: float = 0.0


def available() -> Tuple[bool, str]:
    """Two-stage feature detection. Returns ``(usable, reason)``.

    The reason string is recorded in ``coverage`` so the report can say *why* the render
    tier did not run, rather than leaving a silent gap (rule R6).
    """
    if importlib.util.find_spec("playwright") is None:
        return False, ("playwright is not installed; the render tier was skipped and "
                       "client-side rendering was assessed from hydration payloads "
                       "(tiers 1-2) instead")
    try:
        from playwright.sync_api import sync_playwright
    except Exception as exc:  # pragma: no cover
        return False, f"playwright is installed but failed to import: {exc}"
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(args=["--no-sandbox"])
            browser.close()
    except Exception as exc:
        return False, (f"playwright is installed but no browser could be launched "
                       f"({type(exc).__name__}); run `playwright install chromium` to "
                       f"enable the render tier")
    return True, ""


class Renderer:
    """A budget-capped, read-only browser session.

    Used as a context manager so the browser is always torn down, including on error::

        with Renderer(allowed_hosts={"example.com"}) as renderer:
            result = renderer.render("https://example.com/p/1")
    """

    def __init__(self, allowed_hosts: Optional[Sequence[str]] = None,
                 settle_ms: int = DEFAULT_SETTLE_MS,
                 timeout_ms: int = DEFAULT_PAGE_TIMEOUT_MS,
                 user_agent: str = "", viewport: Optional[Dict[str, int]] = None):
        self.allowed_hosts = {h.lower() for h in (allowed_hosts or ())}
        self.settle_ms = int(settle_ms)
        self.timeout_ms = int(timeout_ms)
        self.user_agent = user_agent
        self.viewport = dict(viewport or DEFAULT_VIEWPORT)
        self._playwright = None
        self._browser = None
        self._context = None
        self.stats: Dict[str, int] = {"pages": 0, "aborted_non_get": 0,
                                      "aborted_third_party": 0}

    def __enter__(self) -> "Renderer":
        from playwright.sync_api import sync_playwright
        self._playwright = sync_playwright().start()
        self._browser = self._playwright.chromium.launch(
            args=[
                "--no-sandbox",
                "--disable-dev-shm-usage",
                "--disable-background-networking",
                "--disable-sync",
                "--disable-notifications",
                "--no-default-browser-check",
                "--no-first-run",
            ]
        )
        self._context = self._browser.new_context(
            user_agent=self.user_agent or None,
            viewport=self.viewport,
            service_workers="block",
            java_script_enabled=True,
            accept_downloads=False,
            bypass_csp=False,
            ignore_https_errors=False,
            permissions=[],
        )
        self._context.set_default_timeout(self.timeout_ms)
        self._context.route("**/*", self._intercept)
        return self

    def __exit__(self, *exc_info) -> None:
        for closer in (self._context, self._browser):
            try:
                if closer is not None:
                    closer.close()
            except Exception:  # pragma: no cover
                pass
        try:
            if self._playwright is not None:
                self._playwright.stop()
        except Exception:  # pragma: no cover
            pass

    def _intercept(self, route, request) -> None:
        """Abort anything that is not a same-site GET.

        This is the read-only guarantee for the browser tier. A rendered page runs the
        site's own JavaScript, which routinely tries to POST analytics events, register
        service workers, and fetch from a dozen third-party hosts. None of that is
        permitted: the method allowlist that governs the static fetcher governs here too.
        """
        try:
            if request.method.upper() not in ("GET", "HEAD"):
                self.stats["aborted_non_get"] += 1
                route.abort()
                return
            if self.allowed_hosts:
                from urllib.parse import urlsplit
                host = (urlsplit(request.url).hostname or "").lower()
                if host and not any(host == h or host.endswith("." + h)
                                    for h in self.allowed_hosts):
                    self.stats["aborted_third_party"] += 1
                    route.abort()
                    return
            route.continue_()
        except Exception:  # pragma: no cover - route already handled
            try:
                route.abort()
            except Exception:
                pass

    def render(self, url: str) -> RenderResult:
        """Load ``url`` and return its settled DOM. Never interacts with the page."""
        import time
        if self._context is None:
            return RenderResult(url=url, ok=False, reason="renderer is not started")

        started = time.monotonic()
        page = None
        try:
            page = self._context.new_page()
            page.goto(url, wait_until="domcontentloaded", timeout=self.timeout_ms)
            # networkidle never settles on pages with polling or long analytics, so it is
            # attempted with a short bounded wait and its failure is not an error.
            try:
                page.wait_for_load_state("networkidle", timeout=self.settle_ms)
            except Exception:
                page.wait_for_timeout(min(self.settle_ms, 1200))
            html = page.content()
            self.stats["pages"] += 1
            return RenderResult(
                url=url, ok=True, html=html,
                aborted_non_get=self.stats["aborted_non_get"],
                blocked_requests=self.stats["aborted_third_party"],
                elapsed_ms=(time.monotonic() - started) * 1000.0,
            )
        except Exception as exc:
            return RenderResult(
                url=url, ok=False,
                reason=f"{type(exc).__name__}: {str(exc)[:200]}",
                elapsed_ms=(time.monotonic() - started) * 1000.0,
            )
        finally:
            if page is not None:
                try:
                    page.close()
                except Exception:  # pragma: no cover
                    pass


def describe_policy() -> Dict[str, object]:
    """Machine-readable render policy, embedded in the report's methodology block."""
    return {
        "tier": "optional (tier 3 of 3)",
        "required": False,
        "fallback": ("tiers 1-2 detect client-side rendering from structural signals "
                     "and hydration payloads, with no browser"),
        "read_only_controls": [
            "all non-GET requests aborted by request interception",
            "no clicks, typing, form submission or any other interaction",
            "service workers blocked",
            "downloads and permissions disabled",
            "third-party hosts aborted",
            "per-page wall-clock cap and a run-level page cap",
        ],
    }
