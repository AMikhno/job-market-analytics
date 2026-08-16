"""The candidate profile, and the prompt block V2 scores postings against.

The profile is private config (ADR-0020 §5), the same posture as the company
list (ADR-0011): the real file is gitignored and a committed example stands in,
so a clone still builds. The path comes from `shared.config.Settings` — nothing
here reads the environment directly.

`render_prompt` output is provenance, not merely text. It is the static prefix
every scoring call shares (so the model's context cache can discount it), and
`PROMPT_VERSION` is written beside each score: a fit_score is only comparable to
another produced by the same wording, and a re-worded rubric has to be
distinguishable from a re-scored posting.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Final

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator

log = logging.getLogger(__name__)

ROOT: Final = Path(__file__).resolve().parents[1]
EXAMPLE_PROFILE: Final = ROOT / "config" / "profile.example.yaml"

# Bump whenever the rendered wording below changes. It lands in int_jobs_scored
# beside every fit_score, and a bump is what triggers re-scoring — so an edit
# that forgets it silently mixes two rubrics in one column (docs/v2-plan.md).
PROMPT_VERSION: Final = "1"

# Each instruction is a measurement from the manual pass recorded in
# docs/research/relevance-signals.md, not a preference. Drop one and V2 rebuilds
# the keyword matcher that exercise disproved:
#   1. requirements-only scoring measured 3/13 against 1/21 for whole-posting;
#   2. the location gate keeps unqualified "Remote", so eligibility has to be
#      reported rather than filtered on;
#   3. the top-ranked posting in the set was titled Manager and described purely
#      individual work, while five were rejected precisely for managing people;
#   4. 123 postings (~9%) named a deal-breaker technology and enough were
#      otherwise strong that deleting them would have cost real matches.
_RUBRIC: Final = """\
You are scoring how well one job posting fits one candidate.

Score 1 to 5, where 5 means the posting's requirements are what this candidate
already does daily, and 1 means the work is unrelated to their field.

Follow these four rules; each corrects a specific measured failure mode.

1. Judge the REQUIREMENTS, not the whole posting. Every corporate posting
   mentions dashboards, reporting and stakeholders somewhere; only a data role
   *requires* SQL modelling, a warehouse or a transformation tool. A
   responsibilities blurb describing reporting is not a data role.
2. Report eligibility, never score on it. Emit ok / restricted / unclear for
   whether the candidate's stated constraints permit the role. A posting they
   cannot take is still reported, it is not scored down for that alone.
3. Infer level from the DESCRIBED SCOPE — direct reports, hiring, headcount,
   budget ownership — not from the title. Where the title and the described
   work disagree, the described work is correct.
4. A deal-breaker technology named in the posting LOWERS the score; it never
   zeroes it. One mention in a nice-to-have line is not a role built on it.
"""


class Profile(BaseModel):
    """What a posting is scored against. Every field is private config."""

    model_config = ConfigDict(extra="forbid")

    target_roles: list[str] = Field(min_length=1)
    core_skills: list[str] = Field(min_length=1)
    nice_to_have_skills: list[str] = Field(default_factory=list)
    seniority: str = Field(min_length=1)
    constraints: list[str] = Field(default_factory=list)
    summary: str = Field(min_length=1)

    @field_validator("target_roles", "core_skills", "nice_to_have_skills", "constraints")
    @classmethod
    def _no_blank_entries(cls, values: list[str]) -> list[str]:
        """Reject blank list entries instead of rendering them as empty bullets.

        A stray trailing "- " in the YAML is invisible in the file and produces a
        prompt line that reads as a missing requirement, which is worse than a
        loud parse failure.
        """
        cleaned = [v.strip() for v in values]
        if any(not v for v in cleaned):
            raise ValueError("blank entry in list")
        return cleaned

    @field_validator("seniority", "summary")
    @classmethod
    def _not_blank(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("must not be blank")
        return cleaned


def _bullets(items: list[str]) -> str:
    """List entries as prompt bullets, in the order given.

    Deliberately not sorted: the order in the file expresses priority, and
    sorting would silently discard it. Determinism comes from the file, which
    does not change between renders.
    """
    return "\n".join(f"- {item}" for item in items) if items else "- (none stated)"


def render_prompt(profile: Profile) -> str:
    """The static, cacheable prompt prefix for one profile. Deterministic."""
    return (
        f"{_RUBRIC}\n"
        f"Candidate summary:\n{profile.summary}\n\n"
        f"Seniority: {profile.seniority}\n\n"
        f"Target roles:\n{_bullets(profile.target_roles)}\n\n"
        f"Core skills (the daily work):\n{_bullets(profile.core_skills)}\n\n"
        f"Nice-to-have skills:\n{_bullets(profile.nice_to_have_skills)}\n\n"
        f"Constraints (report as eligibility, rule 2):\n{_bullets(profile.constraints)}\n"
    )


def _profile_path(profile_yaml: str) -> Path:
    """Resolve the profile file: the private one if present, else the example.

    Mirrors the company list's fallback (`ingest.pipeline._companies_path`) so a
    clone and CI both build without the private file; raises only when neither
    exists, because a silently empty profile would score every posting against
    nothing.
    """
    path = Path(profile_yaml)
    if not path.is_absolute():
        path = ROOT / path
    if path.exists():
        return path
    if EXAMPLE_PROFILE.exists():
        log.warning(
            "profile not found at %s; using the example. Create that file with "
            "your real profile for a real run.",
            path,
        )
        return EXAMPLE_PROFILE
    raise FileNotFoundError(f"no profile at {path} or {EXAMPLE_PROFILE}")


def load_profile(profile_yaml: str) -> Profile:
    """Parse the profile YAML into a validated Profile.

    A malformed profile fails here, before any posting is scored — the same
    posture as the company list, where a bad row fails the run rather than
    quietly skewing it.
    """
    path = _profile_path(profile_yaml)
    parsed: Any = yaml.safe_load(path.read_text())
    if not isinstance(parsed, dict):
        raise ValueError(f"{path} must contain a YAML mapping, got {type(parsed).__name__}")
    return Profile.model_validate(parsed)
