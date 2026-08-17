"""The resume corpus V2 scores postings against, and the prompt it renders.

Private config (ADR-0020 §5, reshaped by ADR-0027), same posture as the company
list (ADR-0011): the real file is gitignored and a committed example stands in
with a warning, so a clone and CI still build.

Work bullets rather than a skills list, and one text per bullet rather than one
blob per resume -- both arguments are in ADR-0027.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Final

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator

log = logging.getLogger(__name__)

ROOT: Final = Path(__file__).resolve().parents[1]
EXAMPLE_RESUME: Final = ROOT / "config" / "resume.example.yaml"

# Bump whenever the rendered wording changes. It lands beside every fit_score and
# a bump is what triggers re-scoring; forgetting it mixes two rubrics in one
# column (docs/v2-plan.md).
PROMPT_VERSION: Final = "2"

# The role families a bullet can be evidence for. Closed on purpose: a typo'd tag
# would silently tag nothing, and a matcher quietly ignoring half the corpus
# looks exactly like one that is working.
ROLE_FAMILIES: Final = frozenset(
    {"analytics-engineer", "forward-deployed-engineer", "gtm-engineer"}
)

# From the LLM pass in docs/research/relevance-signals.md -- argued hypotheses,
# not human-verified measurements (see that file's provenance note). Rule 0 is
# new in PROMPT_VERSION 2: the same pass found relevant work under titles like
# Forward Deployed Engineer and ERP Specialist, so titles are discounted
# outright rather than weighted down.
_RUBRIC: Final = """\
You are scoring how well one job posting fits one candidate, given the
candidate's actual work history below.

Score 1 to 5, where 5 means the posting's requirements describe work this
candidate has demonstrably done, and 1 means the work is unrelated to their
field.

Follow these five rules; each corrects a specific observed failure mode.

0. IGNORE THE POSTING'S TITLE. Companies name a role after the org chart the
   seat sits in, not the work being done. Judge the requirements only. A title
   that sounds unrelated is not evidence of anything.
1. Judge the REQUIREMENTS, not the whole posting. Every corporate posting
   mentions dashboards, reporting and stakeholders somewhere; only a data role
   *requires* SQL modelling, a warehouse or a transformation tool. A
   responsibilities blurb describing reporting is not a data role.
2. Report eligibility, never score on it. Emit ok / restricted / unclear for
   whether the candidate's stated constraints permit the role. A posting they
   cannot take is still reported, it is not scored down for that alone.
3. Infer level from the DESCRIBED SCOPE -- direct reports, hiring, headcount,
   budget ownership -- not from the title. Where the title and the described
   work disagree, the described work is correct.
4. A deal-breaker technology named in the posting LOWERS the score; it never
   zeroes it. One mention in a nice-to-have line is not a role built on it.

Match against the WORK DESCRIBED below, not against the job titles it was done
under. The candidate's own titles are frequently a poor description of it.
"""


class Bullet(BaseModel):
    """One piece of evidence: something built, for whom, and what changed."""

    model_config = ConfigDict(extra="forbid")

    text: str = Field(min_length=1)
    evidences: list[str] = Field(default_factory=list)

    @field_validator("evidences")
    @classmethod
    def _known_families(cls, values: list[str]) -> list[str]:
        unknown = sorted(set(values) - ROLE_FAMILIES)
        if unknown:
            raise ValueError(f"unknown role families {unknown}; expected {sorted(ROLE_FAMILIES)}")
        return values

    @field_validator("text")
    @classmethod
    def _not_blank(cls, value: str) -> str:
        cleaned = " ".join(value.split())
        if not cleaned:
            raise ValueError("must not be blank")
        return cleaned


class Role(BaseModel):
    """One position. `title` is what the work was; `official_title` is what the
    org chart called it, kept because the two disagreeing is the norm."""

    model_config = ConfigDict(extra="forbid")

    org: str = Field(min_length=1)
    title: str = Field(min_length=1)
    period: str = Field(min_length=1)
    location: str | None = None
    industry: str | None = None
    official_title: str | None = None
    progression: str | None = None
    bullets: list[Bullet] = Field(min_length=1)


class Project(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1)
    text: str = Field(min_length=1)
    evidences: list[str] = Field(default_factory=list)

    @field_validator("evidences")
    @classmethod
    def _known_families(cls, values: list[str]) -> list[str]:
        unknown = sorted(set(values) - ROLE_FAMILIES)
        if unknown:
            raise ValueError(f"unknown role families {unknown}; expected {sorted(ROLE_FAMILIES)}")
        return values


class EvidenceUnit(BaseModel):
    """One bullet, flattened with enough context to stand alone.

    The unit an embedding is computed over. It carries its origin because a
    bullet read without the "for whom" loses what distinguishes the role
    families from each other.
    """

    model_config = ConfigDict(extra="forbid")

    unit_id: str
    source: str
    text: str
    evidences: list[str]


class Resume(BaseModel):
    """The corpus a posting is scored against. Every field is private config."""

    model_config = ConfigDict(extra="forbid")

    summary: str = Field(min_length=1)
    seniority: str = Field(min_length=1)
    constraints: list[str] = Field(default_factory=list)
    skills: dict[str, list[str]] = Field(default_factory=dict)
    work_history: list[Role] = Field(min_length=1)
    projects: list[Project] = Field(default_factory=list)
    education: list[str] = Field(default_factory=list)


def evidence_units(resume: Resume) -> list[EvidenceUnit]:
    """Every bullet as a standalone, embeddable unit, in document order.

    Bullet-level rather than whole-resume (ADR-0027). Ordering is the file's, so
    ids are stable across runs unless the file changes.
    """
    units: list[EvidenceUnit] = []
    for role in resume.work_history:
        for i, bullet in enumerate(role.bullets):
            units.append(
                EvidenceUnit(
                    unit_id=f"{role.org}:{role.period}:{i}",
                    source=f"{role.title} at {role.org} ({role.period})",
                    text=bullet.text,
                    evidences=bullet.evidences,
                )
            )
    for project in resume.projects:
        units.append(
            EvidenceUnit(
                unit_id=f"project:{project.name}",
                source=f"Personal project: {project.name}",
                text=project.text,
                evidences=project.evidences,
            )
        )
    return units


def _bullets(items: list[str]) -> str:
    """List entries as prompt bullets, in the order given.

    Not sorted: order in the file expresses priority. Determinism comes from the
    file, which does not change between renders.
    """
    return "\n".join(f"- {item}" for item in items) if items else "- (none stated)"


def render_prompt(resume: Resume) -> str:
    """The static, cacheable prompt prefix for one resume. Deterministic."""
    parts = [
        _RUBRIC,
        f"\nCandidate summary:\n{resume.summary}\n",
        f"Seniority: {resume.seniority}\n",
        f"Constraints (report as eligibility, rule 2):\n{_bullets(resume.constraints)}\n",
    ]
    if resume.skills:
        parts.append("Skills:")
        for group, items in resume.skills.items():
            parts.append(f"  {group}: {', '.join(items)}")
        parts.append("")
    parts.append("Work done (match against this, not against titles):")
    for unit in evidence_units(resume):
        parts.append(f"- [{unit.source}] {unit.text}")
    if resume.education:
        parts.append(f"\nEducation:\n{_bullets(resume.education)}")
    return "\n".join(parts) + "\n"


def _resume_path(resume_yaml: str) -> Path:
    """Resolve the resume file: the private one if present, else the example.

    Mirrors the company list's fallback (`ingest.pipeline._companies_path`).
    Raises when neither exists rather than scoring every posting against nothing.
    """
    path = Path(resume_yaml)
    if not path.is_absolute():
        path = ROOT / path
    if path.exists():
        return path
    if EXAMPLE_RESUME.exists():
        log.warning(
            "resume not found at %s; using the example. Create that file with "
            "your real work history for a real run.",
            path,
        )
        return EXAMPLE_RESUME
    raise FileNotFoundError(f"no resume at {path} or {EXAMPLE_RESUME}")


def load_resume(resume_yaml: str) -> Resume:
    """Parse the resume YAML into a validated Resume.

    A malformed resume fails here, before any posting is scored -- same posture
    as the company list.
    """
    path = _resume_path(resume_yaml)
    parsed: Any = yaml.safe_load(path.read_text())
    if not isinstance(parsed, dict):
        raise ValueError(f"{path} must contain a YAML mapping, got {type(parsed).__name__}")
    return Resume.model_validate(parsed)
