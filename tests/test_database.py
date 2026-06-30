from db.database import SessionLocal, fetch_history, upsert_models
from db.models import MetricSnapshot, Model3D


def _row(**kw):
    base = dict(source="printables", external_id="1", title="T", source_url="u1")
    base.update(kw)
    return base


def test_upsert_inserts_then_updates():
    inserted, updated = upsert_models([_row(downloads_count=10)])
    assert (inserted, updated) == (1, 0)

    # Same (source, external_id) -> update, not a duplicate row.
    inserted, updated = upsert_models([_row(downloads_count=20, title="T2")])
    assert (inserted, updated) == (0, 1)

    db = SessionLocal()
    rows = db.query(Model3D).all()
    db.close()
    assert len(rows) == 1
    assert rows[0].downloads_count == 20
    assert rows[0].title == "T2"


def test_upsert_distinguishes_sources():
    upsert_models([
        _row(source="printables", external_id="1", source_url="u1"),
        _row(source="makerworld", external_id="1", source_url="u2"),
    ])
    db = SessionLocal()
    count = db.query(Model3D).count()
    db.close()
    assert count == 2  # same external_id but different source -> two rows


def test_snapshots_record_only_on_change():
    upsert_models([_row(downloads_count=10, likes_count=1)])
    db = SessionLocal()
    model_id = db.query(Model3D).one().id
    assert db.query(MetricSnapshot).count() == 1
    db.close()

    # Re-scrape with identical metrics -> no new snapshot.
    upsert_models([_row(downloads_count=10, likes_count=1)])
    db = SessionLocal()
    assert db.query(MetricSnapshot).count() == 1
    db.close()

    # Metrics changed -> a new snapshot.
    upsert_models([_row(downloads_count=25, likes_count=3)])
    db = SessionLocal()
    assert db.query(MetricSnapshot).count() == 2
    db.close()

    history = fetch_history(model_id)
    assert [h["downloads_count"] for h in history] == [10, 25]


def test_fetch_history_empty_for_unknown():
    assert fetch_history(999) == []
