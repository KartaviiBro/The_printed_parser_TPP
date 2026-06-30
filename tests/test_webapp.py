import webapp
from db.database import SessionLocal, upsert_models
from db.models import MetricSnapshot


def test_fetch_models_shape():
    upsert_models([dict(source="cults3d", external_id="9", title="Z",
                        source_url="u9", downloads_count=5)])
    rows = webapp.fetch_models()
    assert rows and rows[0]["source"] == "cults3d"
    assert rows[0]["downloads_count"] == 5
    assert "remote_image_url" in rows[0]


def test_clear_models_by_source_then_all():
    upsert_models([
        dict(source="printables", external_id="1", title="A", source_url="u1"),
        dict(source="makerworld", external_id="2", title="B", source_url="u2"),
    ])
    assert webapp.clear_models("printables") == 1
    assert webapp.clear_models() == 1  # clears the remaining row
    assert webapp.fetch_models() == []


def test_filter_rows_by_source_and_query():
    rows = [
        {"source": "printables", "title": "Cool Vase"},
        {"source": "makerworld", "title": "Phone Gear"},
    ]
    assert len(webapp.filter_rows(rows, source="printables")) == 1
    assert webapp.filter_rows(rows, q="gear")[0]["title"] == "Phone Gear"
    assert len(webapp.filter_rows(rows)) == 2


def test_models_to_csv_has_header_and_rows():
    upsert_models([
        dict(source="printables", external_id="1", title="Vase", source_url="u1", downloads_count=10),
        dict(source="makerworld", external_id="2", title="Gear", source_url="u2", downloads_count=20),
    ])
    text = webapp.models_to_csv(webapp.fetch_models())
    header = text.splitlines()[0]
    assert header.startswith("source,title,source_url")
    assert "Vase" in text and "Gear" in text


def test_clear_models_also_removes_snapshots():
    upsert_models([dict(source="printables", external_id="1", title="A",
                        source_url="u1", downloads_count=5)])
    db = SessionLocal()
    assert db.query(MetricSnapshot).count() == 1
    db.close()

    webapp.clear_models()
    db = SessionLocal()
    assert db.query(MetricSnapshot).count() == 0  # no orphaned snapshots
    db.close()
