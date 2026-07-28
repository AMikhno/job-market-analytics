import pytest
import responses

from ingest.adapters.pinpoint import PinpointAdapter
from ingest.sources import PinpointSource
from shared.http import FetchPolicy, build_session

URL_TEMPLATE = PinpointSource(name="pinpoint").url_template
BOARD = "example"


def _adapter() -> PinpointAdapter:
    return PinpointAdapter(URL_TEMPLATE, FetchPolicy(min_interval_s=0))


@responses.activate
def test_pinpoint_maps_common_schema(pinpoint_payload: dict) -> None:
    responses.add(responses.GET, URL_TEMPLATE.format(board_ref=BOARD), json=pinpoint_payload)

    postings = _adapter().fetch(build_session("test/1.0"), BOARD)

    assert len(postings) == 2
    p = postings[0]
    assert p.source == "pinpoint"
    assert p.company == BOARD
    assert p.external_id == "347631"
    assert p.title == "Analytics Engineer"
    assert p.location == "Ottawa, Ontario"
    assert p.remote_policy == "Hybrid"
    assert p.department == "Technical Capability"
    assert p.employment_type == "Full Time"
    assert p.url.endswith("/14d82933-fa60-49a1-b76e-5fefa81baac2")


@responses.activate
def test_pinpoint_body_includes_every_section(pinpoint_payload: dict) -> None:
    """Pinpoint splits a posting into labelled blocks; the responsibilities and
    skills blocks hold most of the tech terms, so the keyword filters need them
    in the one text column they read."""
    responses.add(responses.GET, URL_TEMPLATE.format(board_ref=BOARD), json=pinpoint_payload)

    (p, _) = _adapter().fetch(build_session("test/1.0"), BOARD)

    assert "Build dbt models" in p.description_html
    assert "<h3>Key responsibilities</h3><ul><li>Model data in dbt</li></ul>" in p.description_html
    assert "<li>SQL</li>" in p.description_html
    assert "<h3>Benefits</h3>" in p.description_html


@responses.activate
def test_pinpoint_posting_without_sections_is_just_the_description(pinpoint_payload) -> None:
    responses.add(responses.GET, URL_TEMPLATE.format(board_ref=BOARD), json=pinpoint_payload)

    (_, second) = _adapter().fetch(build_session("test/1.0"), BOARD)

    assert second.description_html == "<div>Keep it up.</div>"


@responses.activate
def test_pinpoint_has_no_post_date_and_does_not_invent_one(pinpoint_payload: dict) -> None:
    """postings.json carries only `deadline_at`. Gold sorts nulls last and the
    digest keys off first_seen_at, so an unknown date is honest; deriving one
    from the deadline would silently mis-rank every Pinpoint posting."""
    responses.add(responses.GET, URL_TEMPLATE.format(board_ref=BOARD), json=pinpoint_payload)

    postings = _adapter().fetch(build_session("test/1.0"), BOARD)

    assert [p.posted_or_updated_at for p in postings] == [None, None]


@responses.activate
def test_pinpoint_location_falls_back_to_the_board_label(pinpoint_payload: dict) -> None:
    """The second posting has no city or province, so its board label is used
    rather than dropping the location entirely."""
    responses.add(responses.GET, URL_TEMPLATE.format(board_ref=BOARD), json=pinpoint_payload)

    (_, second) = _adapter().fetch(build_session("test/1.0"), BOARD)

    assert second.location == "CAN - Remote"
    assert second.department is None  # null department must not raise


@responses.activate
def test_pinpoint_response_without_data_raises() -> None:
    responses.add(responses.GET, URL_TEMPLATE.format(board_ref=BOARD), json={"errors": ["nope"]})

    with pytest.raises(KeyError):
        _adapter().fetch(build_session("test/1.0"), BOARD)
