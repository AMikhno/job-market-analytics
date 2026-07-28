import pytest
import responses

from ingest.adapters.rippling import RipplingAdapter
from ingest.sources import RipplingSource
from shared.http import FetchPolicy, build_session

SOURCE = RipplingSource(name="rippling")
LIST_URL = SOURCE.url_template
DETAIL_URL = SOURCE.detail_url_template
BOARD = "example"
JOB_A = "7527c74e-2e0a-432d-99da-6b3f4042f325"
JOB_B = "cb3c1e2f-06cb-4b5b-bd7b-dbd2489b5985"


def _adapter() -> RipplingAdapter:
    return RipplingAdapter(LIST_URL, DETAIL_URL, FetchPolicy(min_interval_s=0))


def _register(list_payload: list, detail_payload: dict) -> None:
    responses.add(responses.GET, LIST_URL.format(board_ref=BOARD), json=list_payload)
    for job_uuid in (JOB_A, JOB_B):
        responses.add(
            responses.GET,
            DETAIL_URL.format(board_ref=BOARD, job_uuid=job_uuid),
            json=detail_payload,
        )


@responses.activate
def test_rippling_maps_common_schema(rippling_payload, rippling_detail_payload) -> None:
    _register(rippling_payload, rippling_detail_payload)

    postings = _adapter().fetch(build_session("test/1.0"), BOARD)

    p = postings[0]
    assert p.source == "rippling"
    assert p.company == BOARD
    assert p.external_id == JOB_A
    assert p.title == "Analytics Engineer"
    assert p.remote_policy is None  # isRemote false
    assert p.department == "Data"
    assert p.employment_type == "SALARIED_FT"
    assert p.url.endswith(JOB_A)
    assert p.posted_or_updated_at is not None
    assert p.posted_or_updated_at.isoformat() == "2026-07-07T05:34:53.457000-07:00"


@responses.activate
def test_rippling_collapses_the_per_location_duplicates(
    rippling_payload, rippling_detail_payload
) -> None:
    """The list emits one row per job × location, all sharing a uuid. Landed
    as-is they collide on job_key and silver keeps an arbitrary one, so the
    posting's location becomes a coin flip. Three rows here are two jobs."""
    _register(rippling_payload, rippling_detail_payload)

    postings = _adapter().fetch(build_session("test/1.0"), BOARD)

    assert [p.external_id for p in postings] == [JOB_A, JOB_B]
    assert len({p.external_id for p in postings}) == 2
    # one list call + one detail call per *unique* job, not per row
    assert len(responses.calls) == 3


@responses.activate
def test_rippling_location_names_every_place_the_job_is_open(
    rippling_payload, rippling_detail_payload
) -> None:
    _register(rippling_payload, rippling_detail_payload)

    (p, _) = _adapter().fetch(build_session("test/1.0"), BOARD)

    assert p.location == "Ottawa, Canada; Toronto, Canada; Canada"


@responses.activate
def test_rippling_falls_back_to_the_list_locations(
    rippling_payload, rippling_detail_payload
) -> None:
    """If a detail stops carrying workLocations, the labels the list already
    showed are used rather than dropping the location."""
    del rippling_detail_payload["workLocations"]
    _register(rippling_payload, rippling_detail_payload)

    (p, _) = _adapter().fetch(build_session("test/1.0"), BOARD)

    assert p.location == "Ottawa, Canada; Toronto, Canada"


@responses.activate
def test_rippling_maps_the_role_not_the_company_blurb(
    rippling_payload, rippling_detail_payload
) -> None:
    """`description.company` is the same text on every posting of a board."""
    _register(rippling_payload, rippling_detail_payload)

    (p, _) = _adapter().fetch(build_session("test/1.0"), BOARD)

    assert "Build <strong>dbt</strong> models" in p.description_html
    assert "Why Example" not in p.description_html


@responses.activate
def test_rippling_dict_response_raises() -> None:
    """The board API returns a bare array; a dict is an error body or drift."""
    responses.add(responses.GET, LIST_URL.format(board_ref=BOARD), json={"detail": "not found"})

    with pytest.raises(ValueError, match="expected a JSON array"):
        _adapter().fetch(build_session("test/1.0"), BOARD)


@responses.activate
def test_rippling_detail_without_a_role_raises(rippling_payload, rippling_detail_payload) -> None:
    del rippling_detail_payload["description"]["role"]
    _register(rippling_payload, rippling_detail_payload)

    with pytest.raises(KeyError):
        _adapter().fetch(build_session("test/1.0"), BOARD)
