# scraper/makerworld.py
"""MakerWorld scraper (Bambu Lab).

MakerWorld is a Next.js SPA that loads its model lists from a JSON API under
``makerworld.com/api/``. We intercept those JSON responses and deep-search for
design objects — no HTML/CSS parsing, so it's resilient to layout changes.

NOTE: field names below are based on MakerWorld's typical API shape. If a run
yields 0 models, the raw JSON is dumped to ``data/debug/`` so the exact shape
can be confirmed and the mapping finalised (same workflow as Printables).
"""
from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Any, Optional

from scraper.base import BaseScraper, ScrapedModel, deep_find

POPULAR_URL = "https://makerworld.com/en/3d-models?sort=trending"
WEB_BASE = "https://makerworld.com"


class MakerWorldScraper(BaseScraper):
    source = "makerworld"

    async def scrape(self, limit: int = 100) -> list[ScrapedModel]:
        async with self.browser_context() as context:
            page = await context.new_page()
            payloads = await self.collect_api_json(
                page, POPULAR_URL, _is_mw_api, scroll_rounds=8
            )

        seen: dict[str, ScrapedModel] = {}
        for payload in payloads:
            for d in deep_find(payload, _is_mw_model):
                model = _normalise(d)
                if model and model.external_id not in seen:
                    seen[model.external_id] = model
                    if len(seen) >= limit:
                        return list(seen.values())

        models = list(seen.values())
        if not models:
            self.dump_payloads(payloads)
            self.log.error(
                "No MakerWorld models parsed from %d payload(s). Raw JSON in "
                "data/debug/ — share a file with design objects to finalise mapping.",
                len(payloads),
            )
        return models


def _is_mw_api(url: str) -> bool:
    return "makerworld.com/api" in url or "/api/v1/design" in url


def _is_mw_model(d: Any) -> bool:
    if not isinstance(d, dict):
        return False
    has_id = any(k in d for k in ("id", "designId"))
    has_title = isinstance(d.get("title"), str) or isinstance(d.get("name"), str)
    has_metric = any(
        k in d for k in ("likeCount", "downloadCount", "instanceCount", "collectionCount")
    )
    return bool(has_id and has_title and has_metric)


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


def _image(value: Any) -> Optional[str]:
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        return _first(value, "url", "cover", "filePath", "src")
    if isinstance(value, list) and value:
        return _image(value[0])
    return None


def _parse_date(value: Any) -> Optional[datetime]:
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _normalise(d: dict) -> Optional[ScrapedModel]:
    ext = _first(d, "id", "designId")
    title = _first(d, "title", "name")
    if ext is None or not title:
        return None
    ext = str(ext)
    return ScrapedModel(
        source="makerworld",
        external_id=ext,
        title=str(title).strip(),
        source_url=f"{WEB_BASE}/en/models/{ext}",
        remote_image_url=_image(_first(d, "cover", "coverUrl", "image", "thumbnail")),
        description=(_first(d, "summary", "description") or None),
        downloads_count=_to_int(_first(d, "downloadCount", "downloadsCount")),
        likes_count=_to_int(_first(d, "likeCount", "likesCount")),
        published_at=_parse_date(_first(d, "createTime", "createdAt", "publishedAt")),
    )


if __name__ == "__main__":
    from db.database import init_db
    from scraper.logging_setup import setup_logging

    setup_logging()
    init_db()
    asyncio.run(MakerWorldScraper(headless=False).run(limit=100))
