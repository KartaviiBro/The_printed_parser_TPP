# demo.py
"""Seed the database with demo fixtures.

Lets the dashboard/API work out of the box — even offline or after the source
sites change their markup — so a reviewer can run the project and immediately
see populated tables, hype scores and trend charts.

    python demo.py            # seed the default database
    python webapp.py --demo   # seed, then serve the dashboard
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone

from db.database import SessionLocal, init_db, upsert_models
from db.models import MetricSnapshot, Model3D

FIXTURES_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "fixtures", "demo_models.json"
)


def _parse_dt(value):
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return None


def load_demo_data(path: str = FIXTURES_PATH) -> int:
    """Upsert demo models and backfill a little metric history. Idempotent.

    Returns the number of demo models loaded.
    """
    with open(path, encoding="utf-8") as f:
        records = json.load(f)
    for r in records:
        r["published_at"] = _parse_dt(r.get("published_at"))
    upsert_models(records)
    _seed_history(records)
    return len(records)


def _seed_history(records: list[dict], featured: int = 4) -> None:
    """Give the first few models a multi-point download/like history so the
    📈 sparklines have something to show."""
    now = datetime.now(timezone.utc)
    offsets_days = [28, 21, 14, 7, 0]
    db = SessionLocal()
    try:
        for r in records[:featured]:
            model = (
                db.query(Model3D)
                .filter_by(source=r["source"], external_id=r["external_id"])
                .one_or_none()
            )
            if model is None:
                continue
            # Replace with a clean, monotonically growing series ending at the
            # model's current values.
            db.query(MetricSnapshot).filter_by(model_id=model.id).delete()
            final_dl = model.downloads_count or 100
            final_lk = model.likes_count or 10
            for i, days in enumerate(offsets_days):
                scale = (i + 1) / len(offsets_days)
                db.add(
                    MetricSnapshot(
                        model_id=model.id,
                        downloads_count=int(final_dl * scale),
                        likes_count=int(final_lk * scale),
                        captured_at=now - timedelta(days=days),
                    )
                )
        db.commit()
    finally:
        db.close()


if __name__ == "__main__":
    init_db()
    count = load_demo_data()
    print(f"Seeded {count} demo models (with sample metric history).")
