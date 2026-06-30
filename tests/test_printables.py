import asyncio
import json

import scraper.printables as P


def test_devalue_resolver_unflattens_models():
    # SvelteKit __data.json shape: index 0 is root; ints are pointers.
    data = [
        {"items": 1},                                   # 0 root
        [2],                                            # 1 list
        {"id": 3, "name": 4, "slug": 5, "downloadCount": 6},  # 2 model
        "123", "Vase", "vase", 4500,                    # 3..6 scalars
    ]
    payload = {"type": "data", "nodes": [None, {"type": "data", "data": data}]}
    models = P.PrintablesScraper()._extract_models([payload], limit=10)
    assert len(models) == 1
    m = models[0]
    assert m.external_id == "123"
    assert m.title == "Vase"
    assert m.source_url.endswith("/model/123-vase")
    assert m.downloads_count == 4500


def test_looks_like_model_rejects_banner():
    banner = {"id": "26", "name": "Summer Deal", "__typename": "BannerType"}
    assert P._looks_like_model(banner) is False


def test_looks_like_model_accepts_print():
    print_obj = {"id": "1", "name": "X", "slug": "x", "downloadCount": 5}
    assert P._looks_like_model(print_obj) is True


def test_normalise_without_slug_builds_id_url():
    m = P._normalise({"id": "7", "name": "NoSlug", "downloadCount": 3})
    assert m.source_url.endswith("/model/7")
    assert m.downloads_count == 3


def test_extract_next_cursor():
    resp = {"data": {"l": {"pageInfo": {"endCursor": "C1", "hasNextPage": True}}}}
    assert P._extract_next_cursor(resp) == ("C1", True)
    end = {"data": {"l": {"pageInfo": {"endCursor": "C9", "hasNextPage": False}}}}
    assert P._extract_next_cursor(end) == ("C9", False)


def test_paginate_replay_cursor_mode():
    scraper = P.PrintablesScraper()
    req = {"operationName": "PrintList", "query": "print",
           "variables": {"limit": 36, "cursor": None}}
    captured = [("https://api.printables.com/graphql/",
                 {"content-type": "application/json"}, json.dumps(req))]

    class FakeResp:
        ok = True
        status = 200

        def __init__(self, d):
            self._d = d

        async def json(self):
            return self._d

    class FakeReq:
        def __init__(self):
            self.cursors = []

        async def post(self, url, data=None, headers=None, timeout=None):
            body = json.loads(data)
            self.cursors.append(body["variables"]["cursor"])
            idx = len(self.cursors)
            if idx >= 4:
                return FakeResp({"data": {"l": {"items": [],
                                "pageInfo": {"endCursor": "", "hasNextPage": False}}}})
            items = [{"__typename": "PrintProfile", "id": str(idx * 100 + i),
                      "name": f"M{i}", "slug": f"s{i}", "downloadCount": 1}
                     for i in range(36)]
            return FakeResp({"data": {"l": {"items": items,
                            "pageInfo": {"endCursor": f"C{idx}", "hasNextPage": True}}}})

    class FakeCtx:
        def __init__(self):
            self.request = FakeReq()

    ctx = FakeCtx()
    payloads = []
    asyncio.run(scraper._paginate_replay(ctx, captured, payloads, limit=100))
    assert ctx.request.cursors[0] is None
    assert "C1" in ctx.request.cursors  # advanced via cursor
    assert P._count_models(payloads) >= 72
