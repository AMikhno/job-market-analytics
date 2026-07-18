"""Typed source registry. Sources are Pydantic objects, never YAML.

Companies are NOT hardcoded here; they are loaded from the private
config/companies.csv at runtime (see shared.models.Company). This registry
defines *how* to talk to each ATS, not *which* companies — and it is the single
source of truth for board URL templates (adapters are constructed from it).

`{board_ref}` in a template is the ATS-specific path fragment from the company
list. Greenhouse/Lever/Ashby take a bare token; a future multi-parameter ATS (e.g.
Workday's tenant/instance/site) defines its own template and teaches its adapter
to split the ref.
"""

from __future__ import annotations

import re
from typing import Annotated, ClassVar, Literal

from pydantic import BaseModel, Field

# A bare board token: letters/digits then letters/digits/dot/underscore/hyphen.
# No slashes, spaces, or URL punctuation. Fits Greenhouse, Lever, and Ashby.
_BARE_TOKEN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


class SourceBase(BaseModel):
    name: str
    active: bool = True

    # board_ref *format* rule, owned by the source (ADR-0012). Default is a bare
    # token; a multi-segment ATS (e.g. Workday's tenant/instance/site) overrides.
    board_ref_pattern: ClassVar[re.Pattern[str]] = _BARE_TOKEN

    def validate_board_ref(self, board_ref: str) -> None:
        """Raise ValueError if board_ref is malformed for this source.

        Called before any fetch so a bad list (a pasted URL, a slash, a stray
        space) fails loudly instead of building a 404 URL and silently skipping.
        """
        if not self.board_ref_pattern.fullmatch(board_ref):
            raise ValueError(
                f"invalid board_ref {board_ref!r} for source {self.name!r}: "
                "expected a bare board token (no slashes, spaces, or URL)"
            )


class GreenhouseSource(SourceBase):
    adapter: Literal["greenhouse"] = "greenhouse"
    url_template: str = "https://boards-api.greenhouse.io/v1/boards/{board_ref}/jobs?content=true"


class LeverSource(SourceBase):
    adapter: Literal["lever"] = "lever"
    url_template: str = "https://api.lever.co/v0/postings/{board_ref}?mode=json"


class AshbySource(SourceBase):
    adapter: Literal["ashby"] = "ashby"
    url_template: str = (
        "https://api.ashbyhq.com/posting-api/job-board/{board_ref}?includeCompensation=true"
    )


Source = Annotated[GreenhouseSource | LeverSource | AshbySource, Field(discriminator="adapter")]

SOURCES: list[Source] = [
    GreenhouseSource(name="greenhouse"),
    LeverSource(name="lever"),
    AshbySource(name="ashby"),
]
