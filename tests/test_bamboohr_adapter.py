import pytest
import responses

from ingest.adapters.bamboohr import BambooHRAdapter
from ingest.sources import BambooHRSource
from shared.http import FetchPolicy, build_session

SOURCE = BambooHRSource(name="bamboohr")
LIST_URL = SOURCE.url_template
DETAIL_URL = SOURCE.detail_url_template
BOARD = "example"


def _adapter() -> BambooHRAdapter:
    # no sleeping in tests: the interval is exercised in test_http.py
    return BambooHRAdapter(LIST_URL, DETAIL_URL, FetchPolicy(min_interval_s=0))


def _register(list_payload: dict, detail_payload: dict) -> None:
    responses.add(responses.GET, LIST_URL.format(board_ref=BOARD), json=list_payload)
    for job_id in ("82", "83"):
        responses.add(
            responses.GET,
            DETAIL_URL.format(board_ref=BOARD, job_id=job_id),
            json=detail_payload,
        )


@responses.activate
def test_bamboohr_maps_common_schema(bamboohr_list_payload, bamboohr_detail_payload) -> None:
    _register(bamboohr_list_payload, bamboohr_detail_payload)

    postings = _adapter().fetch(build_session("test/1.0"), BOARD)

    assert len(postings) == 2
    p = postings[0]
    assert p.source == "bamboohr"
    assert p.company == BOARD
    assert p.external_id == "82"
    assert p.title == "Analytics Engineer"
    assert p.location == "Ottawa, Ontario, Canada"
    assert p.remote_policy == "Remote"  # from the list's isRemote
    assert p.department == "Data"
    assert p.employment_type == "Full-Time"
    assert p.url == "https://example.bamboohr.com/careers/82"
    assert p.posted_or_updated_at is not None
    assert p.posted_or_updated_at.isoformat() == "2026-05-11T00:00:00+00:00"


@responses.activate
def test_bamboohr_fetches_the_description_from_the_detail_call(
    bamboohr_list_payload, bamboohr_detail_payload
) -> None:
    """The list carries no description at all. Without the detail call the
    posting text would be empty, so silver's deal-breaker filter could never
    fire and desired_tech_hits would always be 0 for 33 companies (ADR-0021)."""
    _register(bamboohr_list_payload, bamboohr_detail_payload)

    postings = _adapter().fetch(build_session("test/1.0"), BOARD)

    assert all(p.description_html for p in postings)
    assert (
        postings[0].description_html == "<p>Build <strong>dbt</strong> models &amp; dashboards.</p>"
    )
    # one list call + one detail call per posting
    assert len(responses.calls) == 3


@responses.activate
def test_bamboohr_raw_keeps_both_payloads(bamboohr_list_payload, bamboohr_detail_payload) -> None:
    """compensation and minimumExperience exist only on the detail; keeping both
    means nothing fetched is discarded before V2 can look at it."""
    _register(bamboohr_list_payload, bamboohr_detail_payload)

    (p, _) = _adapter().fetch(build_session("test/1.0"), BOARD)

    assert p.raw["locationType"] == "1"  # list field
    assert p.raw["detail"]["compensation"] == "$110,000 - $150,000"  # detail field


@responses.activate
def test_bamboohr_location_falls_back_when_the_list_has_none(
    bamboohr_list_payload, bamboohr_detail_payload
) -> None:
    """Second posting's list atsLocation is all nulls, so the detail's location
    is used rather than landing an unknown location."""
    _register(bamboohr_list_payload, bamboohr_detail_payload)

    (_, second) = _adapter().fetch(build_session("test/1.0"), BOARD)

    assert second.location == "Ottawa, Canada"
    assert second.remote_policy is None  # isRemote null -> not asserted as remote


@responses.activate
def test_bamboohr_location_is_none_when_nothing_is_set(
    bamboohr_list_payload, bamboohr_detail_payload
) -> None:
    """A board that fills in no location must yield None (silver keeps unknown
    locations) rather than an empty or comma-only string."""
    for payload in (
        bamboohr_list_payload["result"][0],
        bamboohr_detail_payload["result"]["jobOpening"],
    ):
        payload["atsLocation"] = {"country": None, "state": None, "city": None}
        payload["location"] = {"city": None, "state": None}
    _register(bamboohr_list_payload, bamboohr_detail_payload)

    (p, _) = _adapter().fetch(build_session("test/1.0"), BOARD)

    assert p.location is None


@responses.activate
def test_bamboohr_url_falls_back_to_the_careers_path(
    bamboohr_list_payload, bamboohr_detail_payload
) -> None:
    del bamboohr_detail_payload["result"]["jobOpening"]["jobOpeningShareUrl"]
    _register(bamboohr_list_payload, bamboohr_detail_payload)

    (p, _) = _adapter().fetch(build_session("test/1.0"), BOARD)

    assert p.url == "https://example.bamboohr.com/careers/82"


@responses.activate
def test_bamboohr_response_without_result_raises() -> None:
    """An error body / schema drift must raise (per-company warn in the
    pipeline), not silently look like an empty board."""
    responses.add(responses.GET, LIST_URL.format(board_ref=BOARD), json={"errors": ["nope"]})

    with pytest.raises(KeyError):
        _adapter().fetch(build_session("test/1.0"), BOARD)


@responses.activate
def test_bamboohr_detail_drift_raises(bamboohr_list_payload) -> None:
    """A detail response that stops carrying jobOpening is drift too: the board
    is skipped and reported, not landed with silently missing descriptions."""
    responses.add(responses.GET, LIST_URL.format(board_ref=BOARD), json=bamboohr_list_payload)
    responses.add(
        responses.GET, DETAIL_URL.format(board_ref=BOARD, job_id="82"), json={"result": {}}
    )

    with pytest.raises(KeyError):
        _adapter().fetch(build_session("test/1.0"), BOARD)
