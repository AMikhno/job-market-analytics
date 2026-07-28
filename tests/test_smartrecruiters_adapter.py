import pytest
import responses

from ingest.adapters.smartrecruiters import SmartRecruitersAdapter
from ingest.sources import SmartRecruitersSource
from shared.http import FetchPolicy, build_session

SOURCE = SmartRecruitersSource(name="smartrecruiters")
LIST_URL = SOURCE.url_template
DETAIL_URL = SOURCE.detail_url_template
BOARD = "example"
IDS = ("744000140252429", "744000140258181", "744000140260459")


def _adapter(page_size: int = 2) -> SmartRecruitersAdapter:
    return SmartRecruitersAdapter(
        LIST_URL, DETAIL_URL, FetchPolicy(min_interval_s=0), page_size=page_size
    )


def _register(pages: list[dict], detail_payload: dict) -> None:
    offset = 0
    for page in pages:
        responses.add(
            responses.GET,
            LIST_URL.format(board_ref=BOARD, limit=2, offset=offset),
            json=page,
        )
        offset += len(page["content"])
    for posting_id in IDS:
        responses.add(
            responses.GET,
            DETAIL_URL.format(board_ref=BOARD, posting_id=posting_id),
            json=detail_payload,
        )


@responses.activate
def test_smartrecruiters_maps_common_schema(
    smartrecruiters_payload, smartrecruiters_page2_payload, smartrecruiters_detail_payload
) -> None:
    _register(
        [smartrecruiters_payload, smartrecruiters_page2_payload], smartrecruiters_detail_payload
    )

    postings = _adapter().fetch(build_session("test/1.0"), BOARD)

    p = postings[0]
    assert p.source == "smartrecruiters"
    assert p.company == BOARD
    assert p.external_id == "744000140252429"
    assert p.title == "Analytics Engineer"
    assert p.location == "Ottawa, Ontario, Canada"
    assert p.remote_policy == "Hybrid"
    assert p.department == "Data"
    assert p.employment_type == "Full-time"
    # the list only has an API `ref`; the public URL comes from the detail
    assert p.url == "https://jobs.smartrecruiters.com/Example/744000140252429-analytics-engineer"
    assert p.posted_or_updated_at is not None


@responses.activate
def test_smartrecruiters_walks_every_page(
    smartrecruiters_payload, smartrecruiters_page2_payload, smartrecruiters_detail_payload
) -> None:
    """The list caps at `limit` and reports totalFound; stopping at the first
    page would silently drop every posting past it."""
    _register(
        [smartrecruiters_payload, smartrecruiters_page2_payload], smartrecruiters_detail_payload
    )

    postings = _adapter().fetch(build_session("test/1.0"), BOARD)

    assert [p.external_id for p in postings] == list(IDS)  # 2 + 1 across two pages


@responses.activate
def test_smartrecruiters_stops_when_a_board_misreports_its_total(
    smartrecruiters_payload, smartrecruiters_detail_payload
) -> None:
    """totalFound says 3 but the second page comes back empty: the walk has to
    end on the empty page rather than requesting offsets forever."""
    responses.add(
        responses.GET,
        LIST_URL.format(board_ref=BOARD, limit=2, offset=0),
        json=smartrecruiters_payload,
    )
    responses.add(
        responses.GET,
        LIST_URL.format(board_ref=BOARD, limit=2, offset=2),
        json={"offset": 2, "limit": 2, "totalFound": 3, "content": []},
    )
    for posting_id in IDS[:2]:
        responses.add(
            responses.GET,
            DETAIL_URL.format(board_ref=BOARD, posting_id=posting_id),
            json=smartrecruiters_detail_payload,
        )

    postings = _adapter().fetch(build_session("test/1.0"), BOARD)

    assert len(postings) == 2


@responses.activate
def test_smartrecruiters_body_excludes_the_company_blurb(
    smartrecruiters_payload, smartrecruiters_page2_payload, smartrecruiters_detail_payload
) -> None:
    """companyDescription repeats on every posting of a company; the job's own
    sections are what the keyword filters need."""
    _register(
        [smartrecruiters_payload, smartrecruiters_page2_payload], smartrecruiters_detail_payload
    )

    (p, *_) = _adapter().fetch(build_session("test/1.0"), BOARD)

    assert "Build <strong>dbt</strong> models" in p.description_html
    assert "<h3>Qualifications</h3>" in p.description_html
    assert "does example things" not in p.description_html
    # an empty section contributes no heading
    assert "Additional Information" not in p.description_html


@responses.activate
def test_smartrecruiters_tidies_a_gapped_full_location(
    smartrecruiters_payload, smartrecruiters_page2_payload, smartrecruiters_detail_payload
) -> None:
    """fullLocation leaves an empty slot for a missing region ("Munich, ,
    Germany"); landing that verbatim puts a doubled comma in the column the
    location gate reads."""
    _register(
        [smartrecruiters_payload, smartrecruiters_page2_payload], smartrecruiters_detail_payload
    )

    (_, second, _third) = _adapter().fetch(build_session("test/1.0"), BOARD)

    assert second.location == "Munich, Germany"
    assert second.remote_policy == "Remote"


@responses.activate
def test_smartrecruiters_response_without_content_raises() -> None:
    responses.add(
        responses.GET,
        LIST_URL.format(board_ref=BOARD, limit=2, offset=0),
        json={"message": "company not found"},
    )

    with pytest.raises(KeyError):
        _adapter().fetch(build_session("test/1.0"), BOARD)
