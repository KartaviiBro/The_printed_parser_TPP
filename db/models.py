# db/models.py
"""SQLAlchemy ORM models.

The schema is intentionally source-agnostic so that models scraped from
different platforms (Printables, Thingiverse, Cults3D, MakerWorld, ...) all
live in one table and can be queried uniformly.
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import (
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import declarative_base, relationship

Base = declarative_base()


def _utcnow() -> datetime:
    """Timezone-aware UTC now (datetime.utcnow is deprecated in Py 3.12+)."""
    return datetime.now(timezone.utc)


class Model3D(Base):
    __tablename__ = "parsed_models"

    id = Column(Integer, primary_key=True)

    # --- Provenance: which platform + that platform's own id ---------------
    # `source` lets the same table hold data from many sites without clashes;
    # `external_id` is the platform's stable identifier (survives slug/title
    # changes, unlike source_url). Together they are unique.
    source = Column(String(50), nullable=False, index=True)
    external_id = Column(String(128), nullable=False)

    # --- Core content ------------------------------------------------------
    title = Column(String(512), nullable=False)
    source_url = Column(String(1024), nullable=False, unique=True)
    remote_image_url = Column(String(1024))
    local_image_path = Column(String(1024))
    description = Column(Text)

    # --- Metrics -----------------------------------------------------------
    downloads_count = Column(Integer, default=0)
    likes_count = Column(Integer, default=0)

    # --- Print metadata ----------------------------------------------------
    estimated_weight_g = Column(Integer, nullable=True)
    estimated_time_min = Column(Integer, nullable=True)

    # --- Timestamps --------------------------------------------------------
    published_at = Column(DateTime)
    created_at = Column(DateTime, default=_utcnow)
    updated_at = Column(DateTime, default=_utcnow, onupdate=_utcnow)

    snapshots = relationship(
        "MetricSnapshot",
        back_populates="model",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )

    __table_args__ = (
        # One row per (platform, platform-id). Enables clean upserts.
        UniqueConstraint("source", "external_id", name="uq_source_external_id"),
        Index("ix_source_downloads", "source", "downloads_count"),
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return (
            f"<Model3D source={self.source!r} external_id={self.external_id!r} "
            f"title={self.title!r:.40} downloads={self.downloads_count}>"
        )


class MetricSnapshot(Base):
    """A point-in-time reading of a model's metrics.

    Recording these on every scrape turns the project from a snapshot scraper
    into a time-series: we can plot how downloads/likes grow and compute real
    trending velocity, instead of a one-off popularity number.
    """

    __tablename__ = "metric_snapshots"

    id = Column(Integer, primary_key=True)
    model_id = Column(
        Integer,
        ForeignKey("parsed_models.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    downloads_count = Column(Integer, default=0)
    likes_count = Column(Integer, default=0)
    captured_at = Column(DateTime, default=_utcnow, index=True)

    model = relationship("Model3D", back_populates="snapshots")

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return (
            f"<MetricSnapshot model_id={self.model_id} "
            f"downloads={self.downloads_count} at={self.captured_at}>"
        )
