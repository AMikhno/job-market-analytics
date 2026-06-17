"""Typed source registry. Sources are Pydantic objects, never YAML.

Company slugs are NOT hardcoded here; they are loaded from dbt/seeds/companies.csv
at runtime. This registry defines *how* to talk to each ATS, not *which* companies.
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, Field


class SourceBase(BaseModel):
    name: str
    tier: int = 1
    active: bool = True


class GreenhouseSource(SourceBase):
    adapter: Literal["greenhouse"] = "greenhouse"
    url_template: str = "https://boards-api.greenhouse.io/v1/boards/{slug}/jobs?content=true"


class LeverSource(SourceBase):
    adapter: Literal["lever"] = "lever"
    url_template: str = "https://api.lever.co/v0/postings/{slug}?mode=json"


Source = Annotated[GreenhouseSource | LeverSource, Field(discriminator="adapter")]

SOURCES: list[Source] = [
    GreenhouseSource(name="greenhouse"),
    LeverSource(name="lever"),
]
