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
    bq_location: str = Field(default="northamerica-northeast2")
    # Raw landings are append-only and would grow forever; ingestion-time
    # partitions older than this are dropped (keeps storage under the free tier).
    bq_raw_partition_expiry_days: int = Field(default=400)

    http_user_agent: str = Field(default="job-search-pipeline/0.1")
    # Private company list (gitignored); falls back to the committed example if absent.
    companies_csv: str = Field(default="config/companies.csv")

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

    # A source returning fewer than this many rows is a (non-failing) warning.
    low_volume_threshold: int = Field(default=1)
    # Where the run summary is written for the workflow to read.
    summary_path: str = Field(default="ingest_summary.json")

    @property
    def is_prod(self) -> bool:
        return self.pipeline_target == "prod"


def get_settings() -> Settings:
    """Return a fresh Settings instance (kept a function for test override)."""
    return Settings()
