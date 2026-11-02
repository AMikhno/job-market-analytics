import copy
import logging
from datetime import UTC, datetime

import pytest
import responses
from responses import matchers

from ingest.adapters.workday import WorkdayAdapter, WorkdayRef, _parse_date
from ingest.sources import WorkdaySource
from shared.http import FetchPolicy, build_session
from shared.models import WorkdayLocationRule

SOURCE = WorkdaySource(name="workday")
BOARD = "exampleco/wd3/External_Careers"
REF = WorkdayRef.parse(BOARD)
LIST_URL = REF.fill(SOURCE.url_template)

CANADA = WorkdayLocationRule(countries=["Canada"])
US_REMOTE = WorkdayLocationRule(
    countries=["United States", "United States of America"], location_types=["Remote"]
)
CA_FACET = {"locationHierarchy1": ["ctry-ca"]}
US_REMOTE_FACET = {"locationHierarchy1": ["ctry-us"], "locationHierarchy2": ["type-remote"]}

R100 = "/job/Toronto/Analytics-Engineer_R100"
R101 = "/job/Toronto/Data-Analyst_R101"  # Toronto + US remote: matches both rules
R102 = "/job/Canada-Remote/Data-Engineer_R102"
R103 = "/job/US-Remote/BI-Developer_R103"


def _adapter(
    rules: list[WorkdayLocationRule], described: dict[str, frozenset[str]] | None = None
) -> WorkdayAdapter:
    return WorkdayAdapter(
        SOURCE.url_template,
        SOURCE.detail_url_template,
        SOURCE.public_url_template,
        FetchPolicy(min_interval_s=0),
        locations=rules,
        described=described,
        page_size=2,
    )


def _post(applied: dict, offset: int, payload: dict, limit: int = 2) -> None:
    body = {"appliedFacets": applied, "limit": limit, "offset": offset, "searchText": ""}
    responses.add(
        responses.POST, LIST_URL, json=payload, match=[matchers.json_params_matcher(body)]
    )


def _detail(path: str, payload: dict) -> None:
    responses.add(
        responses.GET, REF.fill(SOURCE.detail_url_template, external_path=path), json=payload
    )


def _single_location(detail: dict, place: str) -> dict:
    out = copy.deepcopy(detail)
    out["jobPostingInfo"].update(location=place, additionalLocations=[])
    out["jobPostingInfo"].pop("externalUrl")
    return out


def _register_board(facets: dict, page1: dict, page2: dict, detail: dict) -> None:
    """Facets probe, the Canada walk (2 pages), the US-remote walk (1 page), details."""
    _post({}, 0, facets, limit=1)
    _post(CA_FACET, 0, page1)
    _post(CA_FACET, 2, page2)
    us_page = {
        "total": 2,
        "jobPostings": [
            page1["jobPostings"][1],  # R101 again
            {
                "title": "BI Developer",
                "externalPath": R103,
                "locationsText": "US, TX, Remote",
                "postedOn": "Posted Yesterday",
                "bulletFields": ["R103"],
            },
        ],
    }
    _post(US_REMOTE_FACET, 0, us_page)
    _detail(R100, _single_location(detail, "Canada, Toronto"))
    _detail(R101, detail)
    _detail(R102, _single_location(detail, "Canada, Remote"))
    _detail(R103, _single_location(detail, "US, TX, Remote"))


def _gets() -> list[str]:
    return [c.request.url for c in responses.calls if c.request.method == "GET"]


@responses.activate
def test_a_new_posting_maps_from_its_detail(
    workday_facets_payload, workday_page1_payload, workday_page2_payload, workday_detail_payload
) -> None:
    _register_board(
        workday_facets_payload, workday_page1_payload, workday_page2_payload, workday_detail_payload
    )

    postings = {
        p.external_id: p
        for p in _adapter([CANADA, US_REMOTE]).fetch(build_session("test/1.0"), BOARD)
    }

    p = postings[R101]
    assert p.source == "workday" and p.company == BOARD
    assert p.title == "Data Analyst"
    # every place, de-duplicated: the posting qualifies through any one of them
    assert p.location == "Canada, Toronto; US, TX, Remote"
    assert p.remote_policy == "Remote"
    assert p.employment_type == "Full time"
    assert p.url.endswith("/External_Careers/job/Toronto/Data-Analyst_R101")
    assert "<strong>dbt</strong>" in p.description_html
    # the real date, not the list's "Posted Today"
    assert p.posted_or_updated_at == datetime(2026, 10, 30, tzinfo=UTC)
    assert postings[R100].remote_policy is None


@responses.activate
def test_rules_are_ored_and_a_posting_matching_both_is_kept_once(
    workday_facets_payload, workday_page1_payload, workday_page2_payload, workday_detail_payload
) -> None:
    """Facets AND across parameters, so "Canada, or US AND Remote" is two
    queries; the multi-location posting comes back from both."""
    _register_board(
        workday_facets_payload, workday_page1_payload, workday_page2_payload, workday_detail_payload
    )

    postings = _adapter([CANADA, US_REMOTE]).fetch(build_session("test/1.0"), BOARD)

    assert [p.external_id for p in postings] == [R100, R101, R102, R103]
    assert len(_gets()) == 4  # one detail each, R101 included only once


@responses.activate
def test_the_walk_keeps_the_first_pages_total(
    workday_facets_payload, workday_page1_payload, workday_page2_payload, workday_detail_payload
) -> None:
    """Page 2 reports total=0. Re-reading the total per page would stop after
    it -- and on a real board, after 40 postings, with no error."""
    _register_board(
        workday_facets_payload, workday_page1_payload, workday_page2_payload, workday_detail_payload
    )

    postings = _adapter([CANADA]).fetch(build_session("test/1.0"), BOARD)

    assert [p.external_id for p in postings] == [R100, R101, R102]


@responses.activate
def test_described_postings_land_from_the_list_without_a_detail_call(
    workday_facets_payload, workday_page1_payload, workday_page2_payload, workday_detail_payload
) -> None:
    _register_board(
        workday_facets_payload, workday_page1_payload, workday_page2_payload, workday_detail_payload
    )
    adapter = _adapter([CANADA], described={BOARD: frozenset({R100, R101})})

    postings = {p.external_id: p for p in adapter.fetch(build_session("test/1.0"), BOARD)}

    assert _gets() == [REF.fill(SOURCE.detail_url_template, external_path=R102)]
    known = postings[R100]
    # empty on purpose: bronze carries the last described landing forward
    assert known.description_html == ""
    assert known.location == "Canada, Toronto"
    assert known.url == "https://exampleco.wd3.myworkdayjobs.com/External_Careers" + R100
    assert postings[R101].location is None  # "2 Locations" is a count, not a place
    assert postings[R102].description_html  # the new one was detailed


@responses.activate
def test_described_ids_of_another_board_do_not_leak() -> None:
    adapter = _adapter([CANADA], described={"other/wd1/Site": frozenset({R100})})

    assert adapter.described.get(BOARD, frozenset()) == frozenset()


def test_no_rules_refuses_to_fetch_the_whole_board() -> None:
    with pytest.raises(ValueError, match="WORKDAY_LOCATIONS"):
        _adapter([]).fetch(build_session("test/1.0"), BOARD)


@responses.activate
def test_a_board_without_any_configured_country_is_an_error(workday_facets_payload) -> None:
    """Not an empty result: it can also mean a facet layout this adapter does
    not read, and that has to reach the run's failed boards."""
    _post({}, 0, workday_facets_payload, limit=1)

    with pytest.raises(LookupError, match="configured countries"):
        _adapter([WorkdayLocationRule(countries=["Nowhere"])]).fetch(
            build_session("test/1.0"), BOARD
        )


@responses.activate
def test_a_rule_whose_type_is_absent_is_dropped_never_widened(workday_facets_payload) -> None:
    """No Remote facet value means no remote postings. Querying the country
    alone instead would pull every US posting on the board."""
    facets = copy.deepcopy(workday_facets_payload)
    location_type = facets["facets"][1]["values"][0]
    location_type["values"] = [v for v in location_type["values"] if v["descriptor"] != "Remote"]
    _post({}, 0, facets, limit=1)

    assert _adapter([US_REMOTE]).fetch(build_session("test/1.0"), BOARD) == []
    assert len(responses.calls) == 1  # the facets probe, nothing else


@responses.activate
def test_hitting_the_list_cap_warns_and_an_empty_page_ends_the_walk(
    workday_facets_payload,
    workday_page1_payload,
    workday_page2_payload,
    workday_detail_payload,
    caplog,
) -> None:
    capped = {**workday_page1_payload, "total": 2000}
    _post({}, 0, workday_facets_payload, limit=1)
    _post(CA_FACET, 0, capped)
    _post(CA_FACET, 2, workday_page2_payload)
    _post(CA_FACET, 3, {"total": 0, "jobPostings": []})
    for path in (R100, R101, R102):
        _detail(path, workday_detail_payload)

    with caplog.at_level(logging.WARNING, logger="ingest.adapters.workday"):
        postings = _adapter([CANADA]).fetch(build_session("test/1.0"), BOARD)

    assert len(postings) == 3
    assert "list cap" in caplog.text
    assert BOARD not in caplog.text  # redacted: Actions logs are public


@responses.activate
def test_a_response_without_postings_raises(workday_facets_payload) -> None:
    _post({}, 0, workday_facets_payload, limit=1)
    _post(CA_FACET, 0, {"errorCode": "HTTP_404"})

    with pytest.raises(KeyError):
        _adapter([CANADA]).fetch(build_session("test/1.0"), BOARD)


@responses.activate
def test_a_detail_without_a_description_raises(
    workday_facets_payload, workday_page1_payload, workday_page2_payload, workday_detail_payload
) -> None:
    """ADR-0021: the posting would land unrankable, so the board fails instead."""
    drifted = copy.deepcopy(workday_detail_payload)
    del drifted["jobPostingInfo"]["jobDescription"]
    _register_board(workday_facets_payload, workday_page1_payload, workday_page2_payload, drifted)

    with pytest.raises(KeyError):
        _adapter([CANADA]).fetch(build_session("test/1.0"), BOARD)


@pytest.mark.parametrize("bad", ["exampleco", "exampleco/wd3", "a//b", "a/b/c/d"])
def test_ref_parsing_rejects_anything_but_three_segments(bad: str) -> None:
    with pytest.raises(ValueError, match="tenant/shard/site"):
        WorkdayRef.parse(bad)


def test_ref_names_its_host() -> None:
    assert REF.host == "exampleco.wd3.myworkdayjobs.com"


def test_dates_keep_an_explicit_zone_and_default_to_utc() -> None:
    assert _parse_date("2026-10-30") == datetime(2026, 10, 30, tzinfo=UTC)
    assert _parse_date("2026-10-30T09:00:00+02:00") == datetime(2026, 10, 30, 7, tzinfo=UTC)
    assert _parse_date(None) is None
