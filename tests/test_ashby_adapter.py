import pytest
import responses

from ingest.adapters.ashby import AshbyAdapter
from ingest.sources import AshbySource
from shared.http import build_session

URL_TEMPLATE = AshbySource(name="ashby").url_template


def _adapter() -> AshbyAdapter:
    return AshbyAdapter(URL_TEMPLATE)


@responses.activate
def test_ashby_maps_common_schema(ashby_payload: dict) -> None:
    board_ref = "example"
    responses.add(responses.GET, URL_TEMPLATE.format(board_ref=board_ref), json=ashby_payload)

    postings = _adapter().fetch(build_session("test/1.0"), board_ref)

    assert len(postings) == 1
    p = postings[0]
    assert p.source == "ashby"
    assert p.company == board_ref
    assert p.external_id == "7458d4e9-da2e-47bd-98cb-adfda43d42b2"
    assert p.title == "Analytics Engineer"
    assert p.location == "Toronto, Canada"
    assert p.remote_policy == "Remote"  # Ashby workplaceType enum
    assert p.department == "Data"
    assert p.employment_type == "FullTime"
    assert p.url.endswith("/7458d4e9-da2e-47bd-98cb-adfda43d42b2")
    assert p.posted_or_updated_at is not None
    assert p.raw["id"] == "7458d4e9-da2e-47bd-98cb-adfda43d42b2"  # original item preserved


@responses.activate
def test_ashby_description_html_is_passed_through(ashby_payload: dict) -> None:
    """Unlike Greenhouse, Ashby's descriptionHtml is already real HTML — the
    adapter must not unescape it (that would corrupt an in-HTML `&amp;`)."""
    board_ref = "example"
    responses.add(responses.GET, URL_TEMPLATE.format(board_ref=board_ref), json=ashby_payload)

    (p,) = _adapter().fetch(build_session("test/1.0"), board_ref)

    assert p.description_html == (
        '<div class="content"><p>Build dbt models &amp; dashboards.</p></div>'
    )


@responses.activate
def test_ashby_response_without_jobs_key_raises() -> None:
    """An error body / schema drift must raise (per-company warn in the
    pipeline), not silently look like an empty board."""
    board_ref = "example"
    responses.add(
        responses.GET, URL_TEMPLATE.format(board_ref=board_ref), json={"errors": ["nope"]}
    )

    with pytest.raises(KeyError):
        _adapter().fetch(build_session("test/1.0"), board_ref)
