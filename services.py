# services.py
"""Shared data-access and export helpers.

Used by both the lightweight stdlib dashboard (`webapp.py`) and the FastAPI
REST layer (`api.py`), so the two front-ends stay in sync.
"""
from __future__ import annotations

import csv
import io
from datetime import datetime

from db.database import SessionLocal
from db.models import MetricSnapshot, Model3D

# Columns included in CSV/JSON exports, in order.
EXPORT_FIELDS = [
    "source", "title", "source_url", "downloads_count", "likes_count",
    "published_at", "created_at", "remote_image_url",
]


def _iso(value) -> str | None:
    return value.isoformat() if isinstance(value, datetime) else None


def fetch_models() -> list[dict]:
    """All models as JSON-serializable dicts, most-downloaded first."""
    db = SessionLocal()
    try:
        rows = (
            db.query(Model3D)
            .order_by(Model3D.downloads_count.desc(), Model3D.id.desc())
            .all()
        )
        return [
            {
                "id": r.id,
                "source": r.source,
                "title": r.title,
                "source_url": r.source_url,
                "remote_image_url": r.remote_image_url,
                "local_image_path": r.local_image_path,
                "description": r.description,
                "downloads_count": r.downloads_count or 0,
                "likes_count": r.likes_count or 0,
                "published_at": _iso(r.published_at),
                "created_at": _iso(r.created_at),
            }
            for r in rows
        ]
    finally:
        db.close()


def clear_models(source: str | None = None) -> int:
    """Delete all models (and their snapshots), or only those of ``source``."""
    db = SessionLocal()
    try:
        q = db.query(Model3D.id)
        if source:
            q = q.filter(Model3D.source == source)
        ids = [row[0] for row in q.all()]
        if not ids:
            return 0
        # Remove snapshots first (SQLite FK cascade isn't enabled by default).
        db.query(MetricSnapshot).filter(
            MetricSnapshot.model_id.in_(ids)
        ).delete(synchronize_session=False)
        deleted = (
            db.query(Model3D)
            .filter(Model3D.id.in_(ids))
            .delete(synchronize_session=False)
        )
        db.commit()
        return deleted
    finally:
        db.close()


def filter_rows(rows: list[dict], source: str | None = None, q: str | None = None) -> list[dict]:
    """Filter model dicts by source and/or a title substring (case-insensitive)."""
    out = rows
    if source:
        out = [r for r in out if r.get("source") == source]
    if q:
        ql = q.lower()
        out = [r for r in out if ql in (r.get("title") or "").lower()]
    return out


def models_to_csv(rows: list[dict]) -> str:
    """Serialize model dicts to CSV text (header + one row per model)."""
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=EXPORT_FIELDS, extrasaction="ignore")
    writer.writeheader()
    for r in rows:
        writer.writerow({k: r.get(k, "") for k in EXPORT_FIELDS})
    return buf.getvalue()
