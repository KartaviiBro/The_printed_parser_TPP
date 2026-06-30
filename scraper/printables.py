# scraper/printables.py
"""Printables scraper.

Printables is a SvelteKit SPA fronted by Cloudflare. The model list is delivered
two ways, and we use both for robustness:

* SvelteKit's own ``__data.json`` endpoint (a *flattened*/devalue-encoded JSON
  blob — the primary source for list pages), and
* the GraphQL API (``api.printables.com/graphql``) for detail/lazy data.

Strategy (resilient to markup changes — we never parse CSS):

1. Open the popular page in a stealthed browser to pass Cloudflare and obtain
   clearance cookies.
2. Fetch ``__data.json`` directly with those cookies (deterministic) *and*
   intercept any GraphQL JSON the page requests.
3. Un-flatten SvelteKit payloads, then deep-search every payload for model
   objects and normalise them.

If nothing parses, the raw payloads are dumped to ``data/debug/`` so the real
shape is visible.
"""
from __future__ import annotations

import asyncio
import json
import os
from datetime import datetime
from typing import Any, Iterator, Optional

from playwright.async_api import BrowserContext, Page, Request, Response

from scraper.base import BaseScraper, ScrapedModel

WEB_BASE = "https://www.printables.com"
MEDIA_BASE = "https://media.printables.com"
GRAPHQL_HINT = "graphql"

# Hard cap on browser scroll iterations (each can trigger one infinite-scroll
# GraphQL page). High enough to reach large limits; stops early once satisfied.
MAX_SCROLL_ROUNDS = 60
# Hard cap on GraphQL replay pages (offset pagination top-up).
MAX_REPLAY_PAGES = 40
# Stop scrolling after this many consecutive rounds with no new models.
SCROLL_STAGNATION_LIMIT = 5


class PrintablesScraper(BaseScraper):
    source = "printables"

    def __init__(
        self,
        *,
        ordering: str = "popular",
        period_days: int = 60,
        **kwargs: Any,
    ) -> None:
        """``ordering`` maps to Printables' ?o= (popular/likes/downloads/latest);
        ``period_days`` widens any time-window the list query exposes (default ~2
        months) so the sample isn't limited to the last few weeks."""
        super().__init__(**kwargs)
        self.ordering = ordering
        self.period_days = period_days

    @property
    def popular_url(self) -> str:
        return f"{WEB_BASE}/model?o={self.ordering}"

    async def scrape(self, limit: int = 100) -> list[ScrapedModel]:
        payloads: list[Any] = []
        captured_requests: list[tuple[str, dict, str]] = []

        async with self.browser_context() as context:
            page = await context.new_page()

            # Listen to BOTH responses (to read JSON) and requests (to learn the
            # exact GraphQL query the site uses, so we can replay it).
            async def on_response(resp: Response) -> None:
                try:
                    if not _is_data_response(resp.url):
                        return
                    ctype = (resp.headers or {}).get("content-type", "").lower()
                    if "json" not in ctype:
                        return
                    payloads.append(await resp.json())
                    self.log.info("Captured JSON from %s", _short(resp.url))
                except Exception as exc:  # noqa: BLE001
                    self.log.debug("skip response: %s", exc)

            def on_request(req: Request) -> None:
                try:
                    if GRAPHQL_HINT in req.url and req.method == "POST" and req.post_data:
                        captured_requests.append((req.url, dict(req.headers), req.post_data))
                except Exception as exc:  # noqa: BLE001
                    self.log.debug("skip request: %s", exc)

            page.on("response", on_response)
            page.on("request", on_request)

            # (1) Navigate — clears Cloudflare, server-renders the first page.
            await page.goto(self.popular_url, wait_until="domcontentloaded")
            await _settle(page, 20_000)
            # Best-effort wait for the grid; never fatal.
            try:
                await page.wait_for_selector("a[href*='/model/']", timeout=12_000)
            except Exception:  # noqa: BLE001
                self.log.warning("Model grid not detected yet — continuing anyway")

            # (2) Scroll the infinite-scroll feed to trigger the site's own
            # paginated GraphQL query (which we capture). Plain wheel + settle —
            # the proven reliable approach — plus a JS scroll-to-bottom nudge.
            stagnant = 0
            last_count = 0
            for i in range(MAX_SCROLL_ROUNDS):
                try:
                    await page.mouse.wheel(0, 12_000)
                    await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                except Exception:  # noqa: BLE001
                    pass
                await _settle(page, 6_000)

                count = _count_models(payloads)
                if count >= limit:
                    self.log.info("Reached target: %d models after %d scrolls", count, i + 1)
                    break
                if count == last_count:
                    stagnant += 1
                    if stagnant >= SCROLL_STAGNATION_LIMIT:
                        await self._click_load_more(page)
                        if _count_models(payloads) == last_count:
                            self.log.info("Feed paused at %d models; will top up via replay", count)
                            break
                        stagnant = 0
                else:
                    stagnant = 0
                    self.log.info("Scrolled: %d models so far", count)
                last_count = _count_models(payloads)

            page.remove_listener("response", on_response)
            page.remove_listener("request", on_request)

            # (3) Top up via GraphQL pagination replay (cursor/offset/page) to go
            # past page 1. Never let a replay error zero-out the browser captures.
            try:
                await self._paginate_replay(context, captured_requests, payloads, limit)
            except Exception:  # noqa: BLE001
                self.log.exception("Pagination replay failed; using captured pages only")

        models = self._extract_models(payloads, limit=limit)
        if not models:
            self._dump_payloads(payloads)
            self.log.error(
                "No models parsed from %d payload(s). Raw JSON dumped to "
                "data/debug/ — open it to inspect the real shape. If you see a "
                "Cloudflare/challenge page, run with --headful and solve it.",
                len(payloads),
            )
        return models

    async def _click_load_more(self, page: Page) -> None:
        """Try clicking a 'Load more'/'Show more' button if the feed paused."""
        for sel in (
            "button:has-text('Load more')",
            "button:has-text('Show more')",
            "button:has-text('Загрузить')",
            "a:has-text('Load more')",
        ):
            try:
                btn = page.locator(sel).first
                if await btn.is_visible(timeout=1500):
                    await btn.click()
                    await _settle(page, 8_000)
                    return
            except Exception:
                continue

    # ------------------------------------------------------------------ #
    # GraphQL replay (offset pagination)
    # ------------------------------------------------------------------ #
    def _find_list_template(
        self, captured_requests: list[tuple[str, dict, str]]
    ) -> Optional[tuple[str, dict, dict]]:
        """Pick a captured GraphQL request that looks like the print list."""
        for url, headers, post_data in captured_requests:
            try:
                body = json.loads(post_data)
            except Exception:
                continue
            for b in (body if isinstance(body, list) else [body]):
                op = str(b.get("operationName") or "")
                query = str(b.get("query") or "")
                variables = b.get("variables")
                if "print" in (op + query).lower() and isinstance(variables, dict):
                    return url, headers, b
        return None

    async def _paginate_replay(
        self,
        context: BrowserContext,
        captured_requests: list[tuple[str, dict, str]],
        payloads: list[Any],
        limit: int,
    ) -> None:
        """Re-send the site's own list query with growing offset until we reach
        ``limit``. We don't hardcode the schema — we reuse the captured query and
        only adjust pagination + time-window variables, so it survives schema
        changes.
        """
        template = self._find_list_template(captured_requests)
        if template is None:
            self.log.debug("No list-query template captured; skipping replay")
            return
        url, headers, base_body = template
        base_vars = base_body.get("variables", {})

        page_size = 36
        for lk in ("limit", "first", "perPage", "pageSize"):
            if isinstance(base_vars.get(lk), int) and base_vars[lk] > 0:
                page_size = base_vars[lk]
                break

        offset_key = next((k for k in ("offset", "skip") if k in base_vars), None)
        has_page = "page" in base_vars
        cursor_key = next(
            (k for k in base_vars if "cursor" in k.lower() or k.lower() in ("after",)),
            None,
        )
        if offset_key is None and not has_page and cursor_key is None:
            self.log.debug("List query exposes no pagination var; single replay only")
        else:
            mode = offset_key or ("page" if has_page else cursor_key)
            self.log.info("Replay pagination mode: %s", mode)

        send_headers = {
            k: v
            for k, v in headers.items()
            if k.lower()
            in (
                "content-type", "authorization", "accept", "origin", "referer",
                "apollographql-client-name", "apollographql-client-version",
            )
        }
        send_headers.setdefault("content-type", "application/json")

        offset = 0
        cursor = base_vars.get(cursor_key) if cursor_key else None
        for page_no in range(MAX_REPLAY_PAGES):
            have = _count_models(payloads)
            if have >= limit:
                break
            body = json.loads(json.dumps(base_body))  # deep copy per page
            v = body["variables"]
            for lk in ("limit", "first", "perPage", "pageSize"):
                if lk in v:
                    v[lk] = page_size
            # Widen any time-window variable to ~period_days (default ~2 months).
            for pk in ("period", "timePeriod", "daysBack", "timeRange", "days"):
                if pk in v and isinstance(v[pk], (int, float)):
                    v[pk] = self.period_days
            if offset_key:
                v[offset_key] = offset
            elif has_page:
                v["page"] = page_no + 1
            elif cursor_key:
                v[cursor_key] = cursor  # None on the first page
            elif page_no > 0:
                break  # no way to paginate this query

            try:
                resp = await context.request.post(
                    url, data=json.dumps(body), headers=send_headers, timeout=20_000
                )
                if not resp.ok:
                    self.log.debug("replay page %d -> HTTP %s", page_no + 1, resp.status)
                    break
                data = await resp.json()
            except Exception as exc:  # noqa: BLE001
                self.log.debug("replay page %d failed: %s", page_no + 1, exc)
                break

            before = _count_models(payloads)
            payloads.append(data)
            after = _count_models(payloads)
            if after == before:  # no new models -> end of catalog
                self.log.info("Replay exhausted at page %d (%d models)", page_no + 1, after)
                break
            offset += page_size
            self.log.info("Replayed page %d -> %d models total", page_no + 1, after)
            if cursor_key:
                cursor, has_next = _extract_next_cursor(data)
                if not cursor or has_next is False:
                    self.log.info("Cursor end reached at page %d", page_no + 1)
                    break

    # ------------------------------------------------------------------ #
    # Parsing
    # ------------------------------------------------------------------ #
    def _extract_models(self, payloads: list[Any], limit: int) -> list[ScrapedModel]:
        seen: dict[str, ScrapedModel] = {}
        for payload in payloads:
            for tree in _candidate_trees(payload):
                for candidate in _iter_model_dicts(tree):
                    model = _normalise(candidate)
                    if model is not None and model.external_id not in seen:
                        seen[model.external_id] = model
                        if len(seen) >= limit:
                            return list(seen.values())
        return list(seen.values())

    def _dump_payloads(self, payloads: list[Any]) -> None:
        debug_dir = os.path.join("data", "debug")
        os.makedirs(debug_dir, exist_ok=True)
        for i, payload in enumerate(payloads):
            path = os.path.join(debug_dir, f"printables_payload_{i}.json")
            try:
                with open(path, "w", encoding="utf-8") as f:
                    json.dump(payload, f, ensure_ascii=False, indent=2)
                keys = list(payload.keys()) if isinstance(payload, dict) else type(payload).__name__
                self.log.error("Saved raw payload -> %s (top-level: %s)", path, keys)
            except Exception as exc:  # noqa: BLE001
                self.log.debug("Could not dump payload %d: %s", i, exc)


# ---------------------------------------------------------------------- #
# SvelteKit (devalue) un-flattening
# ---------------------------------------------------------------------- #
def _candidate_trees(payload: Any) -> Iterator[Any]:
    """Yield the raw payload plus any un-flattened SvelteKit data trees."""
    yield payload
    if isinstance(payload, dict) and "nodes" in payload:
        for node in payload.get("nodes") or []:
            if isinstance(node, dict) and isinstance(node.get("data"), list):
                tree = _resolve_devalue(node["data"])
                if tree is not None:
                    yield tree


def _resolve_devalue(data: list) -> Any:
    """Reconstruct an object tree from SvelteKit's flattened devalue array.

    In this format the array holds every unique value once; containers store
    *indices* into the array instead of nested values. Index 0 is the root.
    """
    if not isinstance(data, list) or not data:
        return None
    cache: dict[int, Any] = {}
    inflight: set[int] = set()

    def rec(idx: Any) -> Any:
        if not isinstance(idx, int):
            return idx
        if idx < 0 or idx >= len(data):  # devalue uses -1 for undefined/holes
            return None
        if idx in cache:
            return cache[idx]
        if idx in inflight:  # cycle guard
            return None
        inflight.add(idx)
        value = data[idx]
        if isinstance(value, list):
            out: Any = [rec(x) for x in value]
        elif isinstance(value, dict):
            out = {k: rec(v) for k, v in value.items()}
        else:
            out = value
        inflight.discard(idx)
        cache[idx] = out
        return out

    return rec(0)


# ---------------------------------------------------------------------- #
# Model detection / normalisation
# ---------------------------------------------------------------------- #
def _is_data_response(url: str) -> bool:
    return (
        "api.printables.com" in url
        or "graphql" in url
        or "__data.json" in url
    )


async def _settle(page: Page, timeout_ms: int) -> None:
    """Wait for the network to go idle; never raise on timeout."""
    from playwright.async_api import TimeoutError as PWTimeout

    try:
        await page.wait_for_load_state("networkidle", timeout=timeout_ms)
    except PWTimeout:
        pass


def _short(url: str, n: int = 70) -> str:
    return url if len(url) <= n else url[:n] + "…"


def _extract_next_cursor(data: Any) -> tuple[Optional[str], Optional[bool]]:
    """Find the next pagination cursor in a GraphQL response.

    Looks for Relay-style ``pageInfo.endCursor``/``hasNextPage`` or any
    ``endCursor``/``nextCursor`` key, so cursor pagination works without
    hardcoding the schema.
    """
    found: dict[str, Any] = {"cursor": None, "has_next": None}

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            cur = node.get("endCursor") or node.get("nextCursor") or node.get("cursor")
            if isinstance(cur, str) and cur:
                found["cursor"] = cur
                if "hasNextPage" in node:
                    found["has_next"] = node.get("hasNextPage")
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(data)
    return found["cursor"], found["has_next"]


def _count_models(payloads: list[Any]) -> int:
    seen: set[str] = set()
    for payload in payloads:
        for tree in _candidate_trees(payload):
            for d in _iter_model_dicts(tree):
                ext = d.get("id")
                if ext is not None:
                    seen.add(str(ext))
    return len(seen)


def _looks_like_model(d: Any) -> bool:
    """Heuristic for a Printables print object."""
    if not isinstance(d, dict):
        return False
    has_id = d.get("id") is not None
    has_name = isinstance(d.get("name"), str) or isinstance(d.get("title"), str)
    has_slug = isinstance(d.get("slug"), str) and bool(d.get("slug"))
    has_metrics = any(
        k in d for k in ("downloadCount", "downloadsCount", "likesCount", "likeCount")
    )
    typename = str(d.get("__typename", "")).lower()
    if not (has_id and has_name):
        return False
    return has_slug or has_metrics or "print" in typename


def _iter_model_dicts(node: Any) -> Iterator[dict]:
    """Recursively yield every dict in a JSON tree that looks like a model."""
    if isinstance(node, dict):
        if _looks_like_model(node):
            yield node
        for value in node.values():
            yield from _iter_model_dicts(value)
    elif isinstance(node, list):
        for item in node:
            yield from _iter_model_dicts(item)


def _first(d: dict, *keys: str) -> Any:
    for k in keys:
        if d.get(k) not in (None, ""):
            return d[k]
    return None


def _to_int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _media_url(image: Any) -> Optional[str]:
    """Resolve a Printables image reference to an absolute URL."""
    if isinstance(image, str):
        path = image
    elif isinstance(image, dict):
        path = _first(image, "filePath", "url", "path")
    elif isinstance(image, list) and image:
        return _media_url(image[0])
    else:
        return None
    if not path:
        return None
    if path.startswith("http"):
        return path
    return f"{MEDIA_BASE}/{path.lstrip('/')}"


def _parse_date(value: Any) -> Optional[datetime]:
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _normalise(d: dict) -> Optional[ScrapedModel]:
    external_id = _first(d, "id")
    if external_id is None:
        return None
    external_id = str(external_id)
    title = _first(d, "name", "title")
    if not title:
        return None

    slug = d.get("slug")
    url = f"{WEB_BASE}/model/{external_id}-{slug}" if slug else f"{WEB_BASE}/model/{external_id}"
    image = _first(d, "image", "images", "thumbnail", "cover")
    published = _first(d, "datePublished", "firstPublish", "publishedAt", "created")

    return ScrapedModel(
        source="printables",
        external_id=external_id,
        title=str(title).strip(),
        source_url=url,
        remote_image_url=_media_url(image),
        description=(_first(d, "summary", "description") or None),
        downloads_count=_to_int(_first(d, "downloadCount", "downloadsCount")),
        likes_count=_to_int(_first(d, "likesCount", "likeCount")),
        published_at=_parse_date(published),
    )


def _has_any_model(payloads: list[Any]) -> bool:
    for payload in payloads:
        for tree in _candidate_trees(payload):
            for _ in _iter_model_dicts(tree):
                return True
    return False


if __name__ == "__main__":
    from db.database import init_db
    from scraper.logging_setup import setup_logging

    setup_logging()
    init_db()
    # headless=False is the most reliable mode for passing Cloudflare manually.
    asyncio.run(PrintablesScraper(headless=False).run(limit=100))
