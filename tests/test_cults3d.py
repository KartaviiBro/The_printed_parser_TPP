import scraper.cults3d as C


def test_external_id_extraction():
    url = "https://cults3d.com/en/3d-model/art/cute-dragon"
    assert C._external_id(url) == "cute-dragon"
    assert C._external_id("https://cults3d.com/en/something/else") is None


def test_page_url_pagination():
    assert C._page_url(1) == C.POPULAR_URL
    assert "page=3" in C._page_url(3)


def test_normalise_fixes_protocol_relative_image():
    item = {"url": "https://cults3d.com/en/3d-model/art/dragon", "title": "Dragon",
            "img": "//cdn/c.jpg", "likes": 5, "downloads": 0}
    m = C._normalise(item)
    assert m.external_id == "dragon"
    assert m.remote_image_url == "https://cdn/c.jpg"
    assert m.likes_count == 5


def test_normalise_rejects_incomplete():
    assert C._normalise({"url": "", "title": "x"}) is None
    assert C._normalise({"url": "https://cults3d.com/no-model-here", "title": "x"}) is None
