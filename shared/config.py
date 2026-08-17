"""Typed runtime configuration. The single place env vars are read."""

from __future__ import annotations

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings, loaded from the environment / .env."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    pipeline_target: str = Field(default="dev")  # "dev" (DuckDB) | "prod" (BigQuery)
    duckdb_path: str = Field(default="./data/jobs.duckdb")

    gcp_project: str = Field(default="")
    bq_dataset: str = Field(default="jobs")
    # us-central1, not a Canadian region: northamerica-northeast2 serves no
    # foundational model at all, and BigQuery's AI functions can only reach a
    # model co-located with the dataset (ADR-0026). No residency requirement
    # applies -- the pipeline reads public job postings.
    bq_location: str = Field(default="us-central1")
    # Raw landings are append-only and would grow forever; ingestion-time
    # partitions older than this are dropped (keeps storage under the free tier).
    # 180 days, not 400: a job posting older than six months is not a lead, and
    # the measured steady state halves with it (docs/research/ingestion-cost.md
    # §4: 167 GB -> 75 GB). Changing this reconciles existing tables on the next
    # run -- see _ensure_bigquery_objects, which cannot rely on create_table.
    bq_raw_partition_expiry_days: int = Field(default=180)

    # Sent to every ATS this polls, so it identifies the caller to third parties.
    # Keep it matching the repo name: it is the only handle an ATS operator has
    # if they want to look up who is calling them.
    http_user_agent: str = Field(default="job-market-analytics/0.1")
    # Private company list (gitignored); falls back to the committed example if absent.
    companies_csv: str = Field(default="config/companies.csv")
    # Private resume corpus (gitignored), same fallback: it is the work history V2
    # scores postings against, so it is real PII -- never a secret, never commitable
    # (ADR-0027).
    resume_yaml: str = Field(default="config/resume.yaml")
    # This repo is public, so its Actions logs are too. Company identifiers are
    # digested before they reach a log line. Default on so CI is safe without
    # config; set REDACT_COMPANY_LOGS=false in your local .env to read names.
    redact_company_logs: bool = Field(default=True)

    # Email digest (deliver/digest.py). Unset SMTP credentials disable the
    # digest (dev/CI); in prod they come from GitHub Actions secrets.
    smtp_host: str = Field(default="smtp.gmail.com")
    smtp_port: int = Field(default=465)
    smtp_user: str = Field(default="")
    smtp_password: str = Field(default="")
    digest_to: str = Field(default="")  # recipient; defaults to smtp_user when empty
    # First-ever digest has no watermark; bootstrap with this lookback window
    # instead of emailing the entire gold table.
    digest_lookback_hours: int = Field(default=26)

    # Boards are fetched concurrently. Politeness is enforced per *host*
    # (shared/http.py), so this only bounds how many boards are in flight —
    # raising it does not make any single ATS's API be hit harder.
    fetch_workers: int = Field(default=8)
    # Default seconds between two requests to the same host; a source can widen
    # its own interval in the registry (ingest/sources.py).
    fetch_min_interval_s: float = Field(default=0.5)

    # A source returning fewer than this many rows is a (non-failing) warning.
    # A source's rows_fetched is a *census* of everything currently on its
    # boards, not a count of new postings: raw is append-only and every run
    # re-lands the whole board. Combined with slow posting rotation, that makes
    # the number stable run to run -- so the useful signal is a sudden *drop*
    # against the source's own recent history, not an absolute floor. A fixed
    # floor cannot serve sources whose sizes span two orders of magnitude: set
    # it for the small ones and a large source can lose 95% silently; set it for
    # the large ones and the small ones warn forever.
    low_volume_threshold: int = Field(default=1)  # absolute: zero rows is always wrong
    # Warn when a source lands under this fraction of its recent median.
    volume_drop_ratio: float = Field(default=0.5)
    # Runs of history to build the median from (~1 week at two runs/day), and
    # the minimum needed before the check means anything -- a new source must
    # not warn on its first runs just for lacking a baseline.
    volume_history_runs: int = Field(default=14)
    volume_min_history: int = Field(default=5)
    # Where the run summary is written for the workflow to read.
    summary_path: str = Field(default="ingest_summary.json")

    @property
    def is_prod(self) -> bool:
        return self.pipeline_target == "prod"


def get_settings() -> Settings:
    """Return a fresh Settings instance (kept a function for test override)."""
    return Settings()
