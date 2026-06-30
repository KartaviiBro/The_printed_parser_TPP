from scraper.base import ScrapedModel, deep_find


def test_scrapedmodel_validity():
    assert ScrapedModel(source="x", external_id="1", title="T", source_url="u").is_valid()
    assert not ScrapedModel(source="x", external_id="", title="T", source_url="u").is_valid()
    assert not ScrapedModel(source="x", external_id="1", title="", source_url="u").is_valid()


def test_scrapedmodel_to_row():
    row = ScrapedModel(source="x", external_id="1", title="T", source_url="u",
                       downloads_count=5).to_row()
    assert row["source"] == "x"
    assert row["downloads_count"] == 5
    assert "remote_image_url" in row


def test_deep_find_collects_matching_dicts():
    tree = {"a": {"id": 1, "name": "n"}, "b": [{"id": 2}, {"x": 3}], "c": "scalar"}
    found = deep_find(tree, lambda d: "id" in d)
    assert {d["id"] for d in found} == {1, 2}


def test_deep_find_handles_empty():
    assert deep_find({}, lambda d: "id" in d) == []
    assert deep_find([], lambda d: "id" in d) == []
    assert deep_find("scalar", lambda d: "id" in d) == []
