from datetime import datetime

from db.database import SessionLocal
from db.models import MetricSnapshot, Model3D
from demo import load_demo_data


def test_load_demo_data_seeds_models_and_history():
    count = load_demo_data()
    assert count > 0

    db = SessionLocal()
    try:
        # All fixture models were inserted, across all three sources.
        assert db.query(Model3D).count() == count
        sources = {s for (s,) in db.query(Model3D.source).distinct()}
        assert sources == {"printables", "makerworld", "cults3d"}

        # published_at parsed into a real datetime (not a leftover string).
        sample = db.query(Model3D).first()
        assert sample.published_at is None or isinstance(sample.published_at, datetime)

        # Featured models got a multi-point history for the charts.
        assert db.query(MetricSnapshot).count() >= 5
    finally:
        db.close()


def test_load_demo_data_is_idempotent():
    load_demo_data()
    load_demo_data()
    db = SessionLocal()
    try:
        # Re-running does not duplicate models (upsert by source+external_id).
        assert db.query(Model3D).count() == db.query(Model3D.external_id).distinct().count()
    finally:
        db.close()
