import scraper.makerworld as M


def test_is_mw_model_requires_id_title_and_metric():
    good = {"id": 77, "title": "Stand", "likeCount": 210, "downloadCount": 3400}
    assert M._is_mw_model(good) is True
    # No engagement metric -> not confidently a model.
    assert M._is_mw_model({"id": 1, "title": "x"}) is False
    assert M._is_mw_model({"likeCount": 5}) is False


def test_normalise_builds_url_and_metrics():
    d = {"id": 77, "title": "Phone Stand", "likeCount": 210, "downloadCount": 3400,
         "cover": "https://img/x.jpg", "createTime": "2025-05-01T00:00:00Z"}
    m = M._normalise(d)
    assert m.external_id == "77"
    assert m.source_url.endswith("/models/77")
    assert m.downloads_count == 3400
    assert m.likes_count == 210
    assert m.remote_image_url == "https://img/x.jpg"


def test_normalise_accepts_designid_fallback():
    m = M._normalise({"designId": 5, "name": "Thing", "downloadCount": 1})
    assert m.external_id == "5"
    assert m.title == "Thing"
