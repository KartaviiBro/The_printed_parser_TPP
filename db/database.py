# db/database.py
"""Database engine, session factory and small persistence helpers."""
from __future__ import annotations

import logging
import os
from contextlib import contextmanager
from typing import Iterable, Iterator

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from db.models import Base, MetricSnapshot, Model3D

log = logging.getLogger(__name__)

# Path to the SQLite file in the project root.
DB_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "database.db"
)
DATABASE_URL = os.getenv("TPP_DATABASE_URL", f"sqlite:///{DB_PATH}")

# check_same_thread=False is only needed/safe for SQLite when sessions may be
# touched from more than one thread (e.g. an async event loop).
_connect_args = {"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {}

engine = create_engine(DATABASE_URL, connect_args=_connect_args, future=True)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine, future=True)


def init_db() -> None:
    """Create all tables if they do not yet exist."""
    Base.metadata.create_all(bind=engine)
    log.info("Database initialised at %s", DATABASE_URL)


@contextmanager
def session_scope() -> Iterator[Session]:
    """Transactional session context: commits on success, rolls back on error."""
    db = SessionLocal()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def get_db() -> Iterator[Session]:
    """Yield a session (FastAPI-style dependency; caller manages the transaction)."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def upsert_models(records: Iterable[dict]) -> tuple[int, int]:
    """Insert new models or update metrics for existing ones.

    Dedup key is (source, external_id). Returns (inserted, updated) counts.
    Each record is a plain dict of Model3D column values.
    """
    inserted = updated = 0
    with session_scope() as db:
        for data in records:
            source = data.get("source")
            external_id = data.get("external_id")
            existing = (
                db.query(Model3D)
                .filter(Model3D.source == source, Model3D.external_id == external_id)
                .one_or_none()
            )
            if existing is None:
                model = Model3D(**data)
                db.add(model)
                db.flush()  # assign model.id for the snapshot FK
                inserted += 1
            else:
                # Refresh mutable fields (metrics, image, title may change).
                for field in (
                    "title",
                    "source_url",
                    "remote_image_url",
                    "description",
                    "downloads_count",
                    "likes_count",
                    "published_at",
                    "estimated_weight_g",
                    "estimated_time_min",
                ):
                    value = data.get(field)
                    if value is not None:
                        setattr(existing, field, value)
                model = existing
                updated += 1

            _record_snapshot(db, model)
    return inserted, updated


def _record_snapshot(db: Session, model: Model3D) -> None:
    """Append a metric snapshot for ``model`` if values changed since the last.

    Skipping unchanged readings keeps the time-series compact while still
    capturing every real movement in downloads/likes.
    """
    last = (
        db.query(MetricSnapshot)
        .filter(MetricSnapshot.model_id == model.id)
        .order_by(MetricSnapshot.captured_at.desc(), MetricSnapshot.id.desc())
        .first()
    )
    downloads = model.downloads_count or 0
    likes = model.likes_count or 0
    if last is not None and last.downloads_count == downloads and last.likes_count == likes:
        return
    db.add(
        MetricSnapshot(model_id=model.id, downloads_count=downloads, likes_count=likes)
    )


def fetch_history(model_id: int) -> list[dict]:
    """Return a model's metric snapshots oldest-first (for charting)."""
    with session_scope() as db:
        rows = (
            db.query(MetricSnapshot)
            .filter(MetricSnapshot.model_id == model_id)
            .order_by(MetricSnapshot.captured_at.asc(), MetricSnapshot.id.asc())
            .all()
        )
        return [
            {
                "captured_at": s.captured_at.isoformat() if s.captured_at else None,
                "downloads_count": s.downloads_count or 0,
                "likes_count": s.likes_count or 0,
            }
            for s in rows
        ]
