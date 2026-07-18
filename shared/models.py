"""Pydantic models. No raw dicts cross module boundaries."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, field_validator


class Company(BaseModel):
    """One row of the (private) company list, config/companies.csv.

    `board_ref` is the ATS-specific path fragment that identifies one company's
    board. For Greenhouse/Lever/Ashby it is a bare token (`boards.greenhouse.io/<ref>`),
    but ATS like Workday need several path segments (tenant/instance/site), so it
    is a *reference the adapter interprets*, not necessarily a single slug.
    The legacy `company_slug` CSV header is accepted as an alias.
    """

    model_config = ConfigDict(frozen=True, populate_by_name=True)

    company_name: str
    source: str
    board_ref: str = Field(validation_alias=AliasChoices("board_ref", "company_slug"))
    active: bool = False
    tier: int = 1
    notes: str = ""

    @field_validator("*", mode="before")
    @classmethod
    def _strip_csv_whitespace(cls, value: object) -> object:
        # Hand-maintained CSVs pick up stray spaces (", lever, shyftlabs", " true");
        # an unstripped source/board_ref silently never matches or builds a bad URL,
        # and " true"/" 1" would fail bool/int parsing. Strip every string cell first.
        return value.strip() if isinstance(value, str) else value

    @field_validator("active", "tier", "notes", mode="before")
    @classmethod
    def _blank_csv_cell_means_default(cls, value: object, info: Any) -> object:
        if value in ("", None):
            return {"active": False, "tier": 1, "notes": ""}[info.field_name]
        return value


class RawPosting(BaseModel):
    """A job posting normalized to the common schema by a source adapter.

    Source-specific field names (Greenhouse vs Lever) are reconciled here, in
    typed + unit-tested Python, so dbt downstream sees one consistent shape.
    """

    model_config = ConfigDict(frozen=True)

    source: str  # "greenhouse" | "lever" | "ashby"
    company: str  # the Company.board_ref this posting was fetched from
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
