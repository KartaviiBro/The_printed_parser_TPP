# scraper/base.py
"""Reusable scraping foundation shared by all platform scrapers.

Design goals
------------
* **Resilient to layout changes** — scrapers prefer *network interception*
  (reading the JSON the site's own frontend requests) over parsing HTML/CSS.
* **Anti-bot friendly** — Playwright launched with a realistic fingerprint and
  ``playwright-stealth`` patches applied to every page.
* **Smart waiting** — helpers wait for *network responses* or selectors, never
  blind ``sleep``.
* **Extensible** — add a new platform by subclassing :class:`BaseScraper` and
  implementing :meth:`scrape`.
"""
from __future__ import annotations

import abc
import logging
import random
from contextlib import asynccontextmanager
from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Any, AsyncIterator, Awaitable, Callable, Optional

from playwright.async_api import (
    BrowserContext,
    Page,
    Response,
    async_playwright,
)
from playwright.async_api import TimeoutError as PlaywrightTimeoutError

from db.database import upsert_models

log = logging.getLogger("scraper")

# A small pool of recent, real desktop UA strings. Rotating reduces the chance
# of a single fingerprint being flagged.
USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
]


@dataclass
class ScrapedModel:
    """Platform-agnostic representation of a single 3D model.

    Scrapers emit these; :meth:`BaseScraper.save` maps them onto the ORM. This
    decouples scraping logic from the database schema.
    """

    source: str
    external_id: str
    title: str
    source_url: str
    remote_image_url: Optional[str] = None
    description: Optional[str] = None
    downloads_count: int = 0
    likes_count: int = 0
    published_at: Optional[datetime] = None
    estimated_weight_g: Optional[int] = None
    estimated_time_min: Optional[int] = None

    def to_row(self) -> dict[str, Any]:
        """Dict of column values suitable for :func:`upsert_models`."""
        return {k: v for k, v in asdict(self).items()}

    def is_valid(self) -> bool:
        return bool(self.external_id and self.title and self.source_url)


class BaseScraper(abc.ABC):
    """Abstract base for platform scrapers.

    Subclasses set :attr:`source` and implement :meth:`scrape`. They get a
    stealthed browser context, network-capture helpers and DB persistence for
    free.
    """

    #: Short platform identifier stored in the DB (e.g. ``"printables"``).
    source: str = "base"

    def __init__(
        self,
        *,
        headless: bool = True,
        nav_timeout_ms: int = 45_000,
        locale: str = "en-US",
    ) -> None:
        self.headless = headless
        self.nav_timeout_ms = nav_timeout_ms
        self.locale = locale
        self.log = logging.getLogger(f"scraper.{self.source}")

    # ------------------------------------------------------------------ #
    # Browser lifecycle
    # ------------------------------------------------------------------ #
    @asynccontextmanager
    async def browser_context(self) -> AsyncIterator[BrowserContext]:
        """Launch a stealthed browser context with a realistic fingerprint.

        ``playwright-stealth`` is applied if available; otherwise we fall back
        to a minimal manual patch so the scraper still runs.
        """
        user_agent = random.choice(USER_AGENTS)
        self.log.info("Launching browser (headless=%s)", self.headless)

        stealth = _load_stealth()

        async with async_playwright() as p:
            browser = await p.chromium.launch(
                headless=self.headless,
                args=[
                    "--disable-blink-features=AutomationControlled",
                    "--no-sandbox",
                    "--disable-dev-shm-usage",
                ],
            )
            context = await browser.new_context(
                user_agent=user_agent,
                locale=self.locale,
                viewport={"width": 1920, "height": 1080},
                device_scale_factor=1,
                java_script_enabled=True,
                timezone_id="Europe/Berlin",
            )
            context.set_default_navigation_timeout(self.nav_timeout_ms)
            context.set_default_timeout(self.nav_timeout_ms)

            if stealth is not None:
                try:
                    await stealth.apply_stealth_async(context)
                    self.log.debug("playwright-stealth applied to context")
                except Exception as exc:  # pragma: no cover - defensive
                    self.log.warning("stealth apply failed (%s); using fallback", exc)
                    await context.add_init_script(_MANUAL_STEALTH_JS)
            else:
                await context.add_init_script(_MANUAL_STEALTH_JS)

            try:
                yield context
            finally:
                await context.close()
                await browser.close()
                self.log.info("Browser closed")

    # ------------------------------------------------------------------ #
    # Network interception helper
    # ------------------------------------------------------------------ #
    async def capture_json(
        self,
        page: Page,
        *,
        url_predicate: Callable[[str], bool],
        trigger: Callable[[], Awaitable[Any]],
        settle_after_first_ms: int = 4_000,
        overall_timeout_ms: int = 30_000,
    ) -> list[Any]:
        """Run ``trigger`` and collect JSON bodies of matching responses.

        This is the core anti-fragility tool: instead of scraping the rendered
        DOM, we read the exact JSON the site's frontend fetches. ``url_predicate``
        selects which responses to keep (e.g. the GraphQL/API endpoint).

        Returns the list of decoded JSON payloads (in arrival order).
        """
        payloads: list[Any] = []

        async def _on_response(resp: Response) -> None:
            try:
                if not url_predicate(resp.url):
                    return
                ctype = (resp.headers or {}).get("content-type", "")
                if "json" not in ctype.lower():
                    return
                data = await resp.json()
                payloads.append(data)
                self.log.info(
                    "Captured JSON from %s (%d bytes)",
                    _short_url(resp.url),
                    len(resp.headers.get("content-length", "0") or "0"),
                )
            except Exception as exc:  # noqa: BLE001 - never let a listener crash
                self.log.debug("Skipped response %s: %s", _short_url(resp.url), exc)

        page.on("response", _on_response)
        try:
            await trigger()
            # Smart wait: let the network settle, then a short grace window so
            # late XHRs (lazy-loaded lists) are captured too — no blind sleep on
            # the happy path.
            try:
                await page.wait_for_load_state(
                    "networkidle", timeout=overall_timeout_ms
                )
            except PlaywrightTimeoutError:
                self.log.debug("networkidle not reached; proceeding with captures")
            if not payloads:
                # Give an explicitly-awaited grace period for the first payload.
                try:
                    await page.wait_for_event(
                        "response",
                        lambda r: url_predicate(r.url),
                        timeout=settle_after_first_ms,
                    )
                except PlaywrightTimeoutError:
                    pass
        finally:
            page.remove_listener("response", _on_response)

        self.log.info("Total matching JSON payloads captured: %d", len(payloads))
        return payloads

    # ------------------------------------------------------------------ #
    # Reusable helpers for subclasses
    # ------------------------------------------------------------------ #
    async def settle(self, page: Page, timeout_ms: int = 8_000) -> None:
        """Wait for the network to go idle; never raise on timeout."""
        try:
            await page.wait_for_load_state("networkidle", timeout=timeout_ms)
        except PlaywrightTimeoutError:
            pass

    async def collect_api_json(
        self,
        page: Page,
        url: str,
        predicate: Callable[[str], bool],
        *,
        scroll_rounds: int = 6,
        settle_ms: int = 8_000,
    ) -> list[Any]:
        """Navigate to ``url`` and collect JSON responses whose URL matches.

        Scrolls a few times to trigger infinite-scroll / lazy API calls. This is
        the generic counterpart to a per-site scraper — most SPA platforms
        (MakerWorld, etc.) expose their list as an XHR/fetch we can simply read.
        """
        payloads: list[Any] = []

        async def _on_response(resp: Response) -> None:
            try:
                if not predicate(resp.url):
                    return
                ctype = (resp.headers or {}).get("content-type", "").lower()
                if "json" not in ctype:
                    return
                payloads.append(await resp.json())
                self.log.info("Captured JSON from %s", _short_url(resp.url))
            except Exception as exc:  # noqa: BLE001
                self.log.debug("skip response: %s", exc)

        page.on("response", _on_response)
        try:
            await page.goto(url, wait_until="domcontentloaded")
            await self.settle(page, settle_ms)
            for _ in range(scroll_rounds):
                await page.mouse.wheel(0, 6_000)
                await self.settle(page, settle_ms)
        finally:
            page.remove_listener("response", _on_response)

        self.log.info("Total JSON payloads captured: %d", len(payloads))
        return payloads

    def dump_payloads(self, payloads: list[Any], prefix: Optional[str] = None) -> None:
        """Persist raw payloads to data/debug/ for shape inspection."""
        import json
        import os

        prefix = prefix or self.source
        debug_dir = os.path.join("data", "debug")
        os.makedirs(debug_dir, exist_ok=True)
        for i, payload in enumerate(payloads):
            path = os.path.join(debug_dir, f"{prefix}_payload_{i}.json")
            try:
                with open(path, "w", encoding="utf-8") as f:
                    json.dump(payload, f, ensure_ascii=False, indent=2)
                keys = list(payload.keys()) if isinstance(payload, dict) else type(payload).__name__
                self.log.error("Saved raw payload -> %s (top-level: %s)", path, keys)
            except Exception as exc:  # noqa: BLE001
                self.log.debug("dump failed: %s", exc)

    # ------------------------------------------------------------------ #
    # Subclass contract
    # ------------------------------------------------------------------ #
    @abc.abstractmethod
    async def scrape(self, limit: int = 50) -> list[ScrapedModel]:
        """Return up to ``limit`` models for this platform."""

    # ------------------------------------------------------------------ #
    # Persistence
    # ------------------------------------------------------------------ #
    def save(self, models: list[ScrapedModel]) -> tuple[int, int]:
        valid = [m for m in models if m.is_valid()]
        dropped = len(models) - len(valid)
        if dropped:
            self.log.warning("Dropped %d invalid/incomplete models", dropped)
        if not valid:
            self.log.warning("Nothing to save")
            return (0, 0)
        inserted, updated = upsert_models(m.to_row() for m in valid)
        self.log.info("Saved: %d new, %d updated", inserted, updated)
        return inserted, updated

    async def run(self, limit: int = 50) -> list[ScrapedModel]:
        """High-level entrypoint: scrape, persist, report. Errors are logged."""
        self.log.info("=== %s scrape started (limit=%d) ===", self.source, limit)
        try:
            models = await self.scrape(limit=limit)
        except Exception:
            self.log.exception("Scrape failed for source=%s", self.source)
            return []
        self.log.info("Scraped %d models", len(models))
        self.save(models)
        self.log.info("=== %s scrape finished ===", self.source)
        return models


# ---------------------------------------------------------------------- #
# Stealth helpers
# ---------------------------------------------------------------------- #
_MANUAL_STEALTH_JS = """
Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
Object.defineProperty(navigator, 'languages', {get: () => ['en-US', 'en']});
Object.defineProperty(navigator, 'plugins', {get: () => [1, 2, 3, 4, 5]});
window.chrome = window.chrome || { runtime: {} };
const originalQuery = window.navigator.permissions && window.navigator.permissions.query;
if (originalQuery) {
  window.navigator.permissions.query = (parameters) => (
    parameters.name === 'notifications'
      ? Promise.resolve({ state: Notification.permission })
      : originalQuery(parameters)
  );
}
"""


def _load_stealth():
    """Return a configured playwright-stealth ``Stealth`` instance, or None."""
    try:
        from playwright_stealth import Stealth  # type: ignore

        return Stealth()
    except Exception:  # pragma: no cover - optional dependency
        log.debug("playwright-stealth not available; using manual fallback")
        return None


def _short_url(url: str, n: int = 80) -> str:
    return url if len(url) <= n else url[:n] + "…"


def deep_find(node: Any, predicate: Callable[[dict], bool]) -> "list[dict]":
    """Recursively collect every dict in a JSON tree matching ``predicate``.

    The generic building block scrapers use to find model objects regardless of
    where the API nests them — resilient to schema/shape changes.
    """
    found: list[dict] = []

    def walk(n: Any) -> None:
        if isinstance(n, dict):
            if predicate(n):
                found.append(n)
            for v in n.values():
                walk(v)
        elif isinstance(n, list):
            for item in n:
                walk(item)

    walk(node)
    return found
