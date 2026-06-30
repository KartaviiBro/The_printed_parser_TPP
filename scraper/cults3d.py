# scraper/cults3d.py
"""Cults3D scraper.

Unlike Printables/MakerWorld, Cults3D is largely **server-rendered HTML** (Rails)
and is not behind aggressive Cloudflare bot-walls. There's no public list JSON to
intercept, so here we extract from the DOM — but defensively:

* We match links to ``/3d-model/`` (the stable URL pattern) rather than brittle
  utility CSS classes, and read title/image/counts from each card with several
  fallbacks.
* Listing pages don't always expose download/like counts; those default to 0 and
  can be enriched later from detail pages.

If extraction returns nothing, the page HTML is saved to ``data/debug/`` for
inspection.
"""
from __future__ import annotations

import asyncio
import os
import re
from typing import Optional

from scraper.base import BaseScraper, ScrapedModel

POPULAR_URL = "https://cults3d.com/en/3d-model-categories/all?sort=popular"
WEB_BASE = "https://cults3d.com"
MAX_PAGES = 20  # safety cap on ?page= pagination

# JS run in the page: collect cards by stable href pattern, not utility classes.
_EXTRACT_JS = r"""
() => {
  const out = [];
  const seen = new Set();
  const anchors = document.querySelectorAll('a[href*="/3d-model/"]');
  for (const a of anchors) {
    const href = a.getAttribute('href') || '';
    if (!href.includes('/3d-model/')) continue;
    const url = href.startsWith('http') ? href : (location.origin + href);
    if (seen.has(url)) continue;

    const card = a.closest('article, li, div') || a;
    const img = card.querySelector('img');
    let title = (a.getAttribute('title') || a.textContent || '').trim();
    if ((!title || title.length < 2) && img) title = (img.getAttribute('alt') || '').trim();
    if (!title) continue;
    seen.add(url);

    const grabNum = (sel) => {
      const el = card.querySelector(sel);
      if (!el) return 0;
      const m = (el.textContent || '').replace(/[\s,]/g, '').match(/(\d+(\.\d+)?)([kK])?/);
      if (!m) return 0;
      let n = parseFloat(m[1]);
      if (m[3]) n *= 1000;
      return Math.round(n);
    };

    out.push({
      url,
      title,
      img: img ? (img.getAttribute('src') || img.getAttribute('data-src') || '') : '',
      likes: grabNum('[class*="like"], [class*="heart"]'),
      downloads: grabNum('[class*="download"]'),
    });
  }
  return out;
}
"""


class Cults3DScraper(BaseScraper):
    source = "cults3d"

    async def scrape(self, limit: int = 100) -> list[ScrapedModel]:
        seen: dict[str, ScrapedModel] = {}
        async with self.browser_context() as context:
            page = await context.new_page()

            # Cults3D is paginated server-side via ?page=N. Walk pages until we
            # reach the limit or a page adds nothing new.
            for page_no in range(1, MAX_PAGES + 1):
                if len(seen) >= limit:
                    break
                url = _page_url(page_no)
                try:
                    await page.goto(url, wait_until="domcontentloaded")
                except Exception as exc:  # noqa: BLE001
                    self.log.warning("page %d load failed: %s", page_no, exc)
                    break
                await self.settle(page, 10_000)
                # Nudge lazy images into loading.
                for _ in range(2):
                    await page.mouse.wheel(0, 5_000)
                    await self.settle(page, 3_000)

                try:
                    raw = await page.evaluate(_EXTRACT_JS)
                except Exception as exc:  # noqa: BLE001
                    self.log.error("DOM extraction failed on page %d: %s", page_no, exc)
                    raw = []

                if page_no == 1 and not raw:
                    await self._dump_html(page)
                    break

                added = 0
                for item in raw:
                    model = _normalise(item)
                    if model and model.external_id not in seen:
                        seen[model.external_id] = model
                        added += 1
                        if len(seen) >= limit:
                            break
                self.log.info(
                    "Cults3D page %d: +%d new (total %d)", page_no, added, len(seen)
                )
                if added == 0:  # no new cards -> end of listing
                    break

        models = list(seen.values())[:limit]
        if not models:
            self.log.error(
                "No Cults3D models parsed. Page HTML dumped to data/debug/ — the "
                "card markup may have changed."
            )
        return models

    async def _dump_html(self, page) -> None:
        debug_dir = os.path.join("data", "debug")
        os.makedirs(debug_dir, exist_ok=True)
        path = os.path.join(debug_dir, "cults3d_page.html")
        try:
            with open(path, "w", encoding="utf-8") as f:
                f.write(await page.content())
            self.log.error("Saved page HTML -> %s", path)
        except Exception as exc:  # noqa: BLE001
            self.log.debug("HTML dump failed: %s", exc)


def _page_url(page_no: int) -> str:
    sep = "&" if "?" in POPULAR_URL else "?"
    return POPULAR_URL if page_no <= 1 else f"{POPULAR_URL}{sep}page={page_no}"


_SLUG_RE = re.compile(r"/3d-model/[^/]+/([^/?#]+)")


def _external_id(url: str) -> Optional[str]:
    m = _SLUG_RE.search(url)
    return m.group(1) if m else None


def _normalise(item: dict) -> Optional[ScrapedModel]:
    url = item.get("url")
    title = item.get("title")
    if not url or not title:
        return None
    ext = _external_id(url)
    if not ext:
        return None
    img = item.get("img") or None
    if img and img.startswith("//"):
        img = "https:" + img
    return ScrapedModel(
        source="cults3d",
        external_id=ext,
        title=str(title).strip(),
        source_url=url,
        remote_image_url=img,
        downloads_count=int(item.get("downloads") or 0),
        likes_count=int(item.get("likes") or 0),
    )


if __name__ == "__main__":
    from db.database import init_db
    from scraper.logging_setup import setup_logging

    setup_logging()
    init_db()
    asyncio.run(Cults3DScraper(headless=False).run(limit=100))
