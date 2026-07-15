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

    slack_webhook_url: str = Field(default="")
    http_user_agent: str = Field(default="job-search-pipeline/0.1")
    # Private company list (gitignored); falls back to the committed example if absent.
    companies_csv: str = Field(default="config/companies.csv")

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
