"""Pydantic models. No raw dicts cross module boundaries."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict


class RawPosting(BaseModel):
    """A job posting normalized to the common schema by a source adapter.

    Source-specific field names (Greenhouse vs Lever) are reconciled here, in
    typed + unit-tested Python, so dbt downstream sees one consistent shape.
    """

    model_config = ConfigDict(frozen=True)

    source: str  # "greenhouse" | "lever"
    company: str  # board token / site slug (one company each)
    external_id: str
    title: str
    location: str | None = None
    remote_policy: str | None = None  # Lever workplaceType; null for Greenhouse in V1
    department: str | None = None
    employment_type: str | None = None
    url: str
    description_html: str
    posted_or_updated_at: datetime | None = None
    raw: dict[str, Any]  # original API item, preserved for debugging


class IngestRun(BaseModel):
    """One row of run metadata per source per run -> ops.ingest_runs."""

    run_id: str
    source: str
    company_count: int
    rows_fetched: int
    status: str  # "ok" | "error"
    started_at: datetime
    finished_at: datetime
    error: str | None = None
