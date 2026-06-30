# api.py
"""FastAPI REST layer over the scraped data, with auto-generated OpenAPI docs.

This is an *optional* companion to the zero-dependency dashboard (`webapp.py`):
it exposes the same data as a documented, typed REST API. Run it with:

    pip install -r requirements-api.txt
    uvicorn api:app --reload          # docs at http://127.0.0.1:8000/docs
"""
from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Optional

from fastapi import FastAPI, HTTPException, Query, Response
from pydantic import BaseModel

from db.database import fetch_history, init_db
from scraper import SCRAPERS
from services import fetch_models, filter_rows, models_to_csv


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()  # ensure tables exist before serving
    yield


app = FastAPI(
    title="The Printed Parser API",
    version="1.0.0",
    description="Browse and export 3D models scraped from Printables, "
    "MakerWorld and Cults3D, with per-model metric history.",
    lifespan=lifespan,
)


# --------------------------------------------------------------------------- #
# Schemas
# --------------------------------------------------------------------------- #
class ModelOut(BaseModel):
    id: int
    source: str
    title: str
    source_url: str
    remote_image_url: Optional[str] = None
    description: Optional[str] = None
    downloads_count: int = 0
    likes_count: int = 0
    published_at: Optional[str] = None
    created_at: Optional[str] = None


class SnapshotOut(BaseModel):
    captured_at: Optional[str] = None
    downloads_count: int = 0
    likes_count: int = 0


# --------------------------------------------------------------------------- #
# Routes
# --------------------------------------------------------------------------- #
@app.get("/sources", response_model=list[str], tags=["meta"])
def list_sources() -> list[str]:
    """Registered scraper platforms."""
    return sorted(SCRAPERS)


@app.get("/models", response_model=list[ModelOut], tags=["models"])
def list_models(
    source: Optional[str] = Query(None, description="Filter by platform"),
    q: Optional[str] = Query(None, description="Title substring search"),
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
) -> list[dict]:
    """List models, newest/most-popular first, with optional filtering."""
    rows = filter_rows(fetch_models(), source, q)
    return rows[offset : offset + limit]


@app.get("/models/{model_id}", response_model=ModelOut, tags=["models"])
def get_model(model_id: int) -> dict:
    """A single model by its database id."""
    for row in fetch_models():
        if row["id"] == model_id:
            return row
    raise HTTPException(status_code=404, detail="model not found")


@app.get("/models/{model_id}/history", response_model=list[SnapshotOut], tags=["models"])
def get_model_history(model_id: int) -> list[dict]:
    """Time-series of a model's downloads/likes (oldest first)."""
    return fetch_history(model_id)


@app.get("/export.csv", tags=["export"])
def export_csv(source: Optional[str] = None, q: Optional[str] = None) -> Response:
    """Export the (optionally filtered) models as a CSV download."""
    rows = filter_rows(fetch_models(), source, q)
    body = "﻿" + models_to_csv(rows)  # BOM for Excel
    return Response(
        content=body,
        media_type="text/csv",
        headers={"Content-Disposition": 'attachment; filename="tpp_models.csv"'},
    )
