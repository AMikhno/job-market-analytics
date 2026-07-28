import pytest
import responses

from ingest.adapters.recruitee import RecruiteeAdapter
from ingest.sources import RecruiteeSource
from shared.http import FetchPolicy, build_session

URL_TEMPLATE = RecruiteeSource(name="recruitee").url_template
BOARD = "example"


def _adapter() -> RecruiteeAdapter:
    return RecruiteeAdapter(URL_TEMPLATE, FetchPolicy(min_interval_s=0))


@responses.activate
def test_recruitee_maps_common_schema(recruitee_payload: dict) -> None:
    responses.add(responses.GET, URL_TEMPLATE.format(board_ref=BOARD), json=recruitee_payload)

    postings = _adapter().fetch(build_session("test/1.0"), BOARD)

    assert len(postings) == 2
    p = postings[0]
    assert p.source == "recruitee"
    assert p.company == BOARD
    assert p.external_id == "2684982"
    assert p.title == "Analytics Engineer"
    assert p.location == "Ottawa, Ontario, Canada"
    assert p.remote_policy == "Hybrid"
    assert p.department == "Data Platform Lab"
    assert p.employment_type == "fulltime_permanent"
    assert p.url == "https://example.recruitee.com/o/analytics-engineer"
    assert p.posted_or_updated_at is not None
    assert p.posted_or_updated_at.isoformat() == "2026-07-22T14:20:52+00:00"


@responses.activate
def test_recruitee_body_joins_description_and_requirements(recruitee_payload: dict) -> None:
    """Recruitee splits the posting in two, and the requirements block is where
    most of the tech terms live — dropping it would gut the keyword signals."""
    responses.add(responses.GET, URL_TEMPLATE.format(board_ref=BOARD), json=recruitee_payload)

    (p, other) = _adapter().fetch(build_session("test/1.0"), BOARD)

    assert "Build dbt models" in p.description_html
    assert "<li>SQL</li>" in p.description_html
    # an empty requirements block must not leave a trailing separator
    assert other.description_html == "<p>Own the warehouse.</p>"


@responses.activate
def test_recruitee_falls_back_to_location_parts(recruitee_payload: dict) -> None:
    """The second offer has no preformatted `location`, so the city/state/country
    fields are assembled instead of landing an unknown location."""
    responses.add(responses.GET, URL_TEMPLATE.format(board_ref=BOARD), json=recruitee_payload)

    (_, second) = _adapter().fetch(build_session("test/1.0"), BOARD)

    assert second.location == "Vancouver, British Columbia, Canada"
    assert second.remote_policy == "Remote"


@responses.activate
def test_recruitee_response_without_offers_raises() -> None:
    responses.add(responses.GET, URL_TEMPLATE.format(board_ref=BOARD), json={"error": "nope"})

    with pytest.raises(KeyError):
        _adapter().fetch(build_session("test/1.0"), BOARD)
