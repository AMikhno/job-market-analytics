"""Pydantic models. No raw dicts cross module boundaries."""

from __future__ import annotations

from datetime import date, datetime
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
    # The company's own domain. Not read by the pipeline -- it is the *recovery
    # key*: when a company moves ATS, its website is what lets the discovery tool
    # re-derive a board_ref. Losing it is why a dead board previously meant
    # hunting for the original spreadsheet. Optional, so older lists still parse.
    website: str = ""
    notes: str = ""

    @field_validator("*", mode="before")
    @classmethod
    def _strip_csv_whitespace(cls, value: object) -> object:
        # Hand-maintained CSVs pick up stray spaces (", lever, acme", " true");
        # an unstripped source/board_ref silently never matches or builds a bad URL,
        # and " true"/" 1" would fail bool/int parsing. Strip every string cell first.
        return value.strip() if isinstance(value, str) else value

    @field_validator("active", "tier", "website", "notes", mode="before")
    @classmethod
    def _blank_csv_cell_means_default(cls, value: object, info: Any) -> object:
        if value in ("", None):
            return {"active": False, "tier": 1, "website": "", "notes": ""}[info.field_name]
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


class SourceSummary(BaseModel):
    """Per-source outcome as recorded in the run summary file."""

    source: str
    rows: int
    status: str  # "ok" | "error"
    company_count: int = 0
    error: str | None = None  # may embed raw board_refs: private sinks only
    # Boards that failed while the source as a whole succeeded (a company moved
    # ATS, a board was taken down). Stored **redacted** so this field is safe to
    # print anywhere, including a public CI log; `make whois REF=…` maps one back
    # to a company locally. The raw refs live in `error` / ops.ingest_runs only.
    skipped_refs: list[str] = []


class VolumeDrop(BaseModel):
    """A source that landed far fewer rows than its own recent runs.

    `rows_fetched` is a census of what is on the boards, so it is stable while
    boards are healthy. A large drop means boards stopped answering or companies
    fell out of the list -- neither of which shows up as a failure.
    """

    source: str
    rows: int
    baseline: int  # median of the recent successful runs used for comparison


class RunSummary(BaseModel):
    """The ingest run summary handed to the digest (ingest_summary.json).

    Typed rather than a raw dict so the digest can state source health
    positively -- an absent file is a distinct, reportable case, not an
    empty warnings list.
    """

    run_id: str | None = None
    failures: list[str] = []
    warnings: list[str] = []
    sources: list[SourceSummary] = []
    # Registered, active sources that got no active companies from the list, so
    # they never ran and have no SourceSummary. Reported separately because the
    # per-source health line cannot express them: it counts only what ran, so a
    # source missing from the company list reads as "all sources healthy" while
    # its raw table quietly ages out. Source names are ATS names, not company
    # identifiers -- safe to print unredacted in a public log.
    unconfigured: list[str] = []
    volume_drops: list[VolumeDrop] = []

    @property
    def board_count(self) -> int:
        return sum(s.company_count for s in self.sources)

    @property
    def skipped_refs(self) -> list[str]:
        """Every redacted skipped-board ref across sources, in run order."""
        return [ref for s in self.sources for ref in s.skipped_refs]


# --- Market snapshot: the agg_market_* rows published to the project site ------
#
# These models are the allowlist (ADR-0029). The export selects exactly their
# fields and forbids extras, so a column nobody declared here -- a company, a
# location -- cannot reach the public page by being added to a dbt model. The
# company floor is re-asserted on every row: dbt suppresses, and this refuses to
# publish if suppression ever stops working.

# Mirrors the dbt var `market_min_companies`; tests/test_market_snapshot.py
# fails if the two drift apart.
MARKET_MIN_COMPANIES = 5


class _MarketRow(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class MarketDay(_MarketRow):
    observed_on: date
    open_postings: int = Field(ge=0)
    new_postings: int = Field(ge=0)
    companies: int = Field(ge=MARKET_MIN_COMPANIES)


class MarketTenureStep(_MarketRow):
    days_on_board: int = Field(ge=0)
    at_risk: int = Field(ge=0)
    at_risk_companies: int = Field(ge=MARKET_MIN_COMPANIES)
    closed: int = Field(ge=0)
    survival: float = Field(ge=0, le=1)


class MarketRole(_MarketRow):
    role_family: str
    open_postings: int = Field(ge=0)
    companies: int = Field(ge=MARKET_MIN_COMPANIES)
    remote_postings: int = Field(ge=0)
    hybrid_postings: int = Field(ge=0)
    onsite_postings: int = Field(ge=0)
    unstated_postings: int = Field(ge=0)


class MarketTech(_MarketRow):
    tech: str
    category: str
    postings: int = Field(ge=0)
    companies: int = Field(ge=MARKET_MIN_COMPANIES)
    share_of_postings: float = Field(ge=0, le=1)


class MarketAts(_MarketRow):
    ats: str
    open_postings: int = Field(ge=0)
    boards: int = Field(ge=MARKET_MIN_COMPANIES)


class MarketSnapshot(_MarketRow):
    """market.json: what Ingest exports and the site build renders."""

    generated_at: datetime
    min_companies: int
    daily: list[MarketDay]
    tenure: list[MarketTenureStep]
    roles: list[MarketRole]
    tech: list[MarketTech]
    ats: list[MarketAts]
