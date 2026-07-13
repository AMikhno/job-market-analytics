"""Typed source registry. Sources are Pydantic objects, never YAML.

Companies are NOT hardcoded here; they are loaded from the private
config/companies.csv at runtime (see shared.models.Company). This registry
defines *how* to talk to each ATS, not *which* companies — and it is the single
source of truth for board URL templates (adapters are constructed from it).

`{board_ref}` in a template is the ATS-specific path fragment from the company
list. Greenhouse/Lever take a bare token; a future multi-parameter ATS (e.g.
Workday's tenant/instance/site) defines its own template and teaches its adapter
to split the ref.
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, Field


class SourceBase(BaseModel):
    name: str
    active: bool = True


class GreenhouseSource(SourceBase):
    adapter: Literal["greenhouse"] = "greenhouse"
    url_template: str = "https://boards-api.greenhouse.io/v1/boards/{board_ref}/jobs?content=true"


class LeverSource(SourceBase):
    adapter: Literal["lever"] = "lever"
    url_template: str = "https://api.lever.co/v0/postings/{board_ref}?mode=json"


Source = Annotated[GreenhouseSource | LeverSource, Field(discriminator="adapter")]

SOURCES: list[Source] = [
    GreenhouseSource(name="greenhouse"),
    LeverSource(name="lever"),
]
