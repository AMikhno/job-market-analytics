import pytest
import responses

from ingest.adapters.workable import WorkableAdapter
from ingest.sources import WorkableSource
from shared.http import FetchPolicy, build_session

URL_TEMPLATE = WorkableSource(name="workable").url_template
BOARD = "example"


def _adapter() -> WorkableAdapter:
    return WorkableAdapter(URL_TEMPLATE, FetchPolicy(min_interval_s=0))


def _register(payload: dict) -> None:
    responses.add(responses.GET, URL_TEMPLATE.format(board_ref=BOARD), json=payload)


@responses.activate
def test_workable_maps_common_schema(workable_payload: dict) -> None:
    _register(workable_payload)

    (p,) = _adapter().fetch(build_session("test/1.0"), BOARD)

    assert p.source == "workable"
    assert p.company == BOARD
    assert p.external_id == "2003E0ABE6"  # shortcode: the id Workable's URLs use
    assert p.title == "Analytics Engineer"
    assert p.location == "Kanata, Ontario, Canada"
    assert p.remote_policy == "Remote"  # telecommuting
    assert p.department == "Data"
    assert p.employment_type == "Full-time"
    assert p.url == "https://apply.workable.com/j/2003E0ABE6"
    assert p.posted_or_updated_at is not None
    assert p.posted_or_updated_at.isoformat() == "2026-07-15T00:00:00+00:00"


@responses.activate
def test_workable_uses_the_job_description_not_the_company_blurb(workable_payload: dict) -> None:
    """The account-level `description` is a company blurb. Mapping it would give
    every posting of a company identical text, so the keyword filters would see
    the same words on every row regardless of the actual job."""
    _register(workable_payload)

    (p,) = _adapter().fetch(build_session("test/1.0"), BOARD)

    assert p.description_html == "<p>Build <strong>dbt</strong> models &amp; dashboards.</p>"
    assert "company things" not in p.description_html


@responses.activate
def test_workable_blank_fields_land_as_null(workable_payload: dict) -> None:
    """A live board writes an unset employment_type as "" rather than null;
    landing that verbatim puts empty strings in a column that should be NULL."""
    workable_payload["jobs"][0]["employment_type"] = ""
    workable_payload["jobs"][0]["department"] = ""
    _register(workable_payload)

    (p,) = _adapter().fetch(build_session("test/1.0"), BOARD)

    assert p.employment_type is None
    assert p.department is None


@responses.activate
def test_workable_url_falls_back_to_the_shortlink(workable_payload: dict) -> None:
    del workable_payload["jobs"][0]["url"]
    _register(workable_payload)

    (p,) = _adapter().fetch(build_session("test/1.0"), BOARD)

    assert p.url == "https://apply.workable.com/j/2003E0ABE6"


@responses.activate
def test_workable_response_without_jobs_raises() -> None:
    _register({"name": "Example Inc.", "description": "<p>blurb</p>"})

    with pytest.raises(KeyError):
        _adapter().fetch(build_session("test/1.0"), BOARD)
