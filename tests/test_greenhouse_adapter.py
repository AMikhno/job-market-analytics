import responses

from ingest.adapters.greenhouse import URL_TEMPLATE, GreenhouseAdapter
from shared.http import build_session


@responses.activate
def test_greenhouse_maps_common_schema(greenhouse_payload: dict) -> None:
    slug = "example"
    responses.add(responses.GET, URL_TEMPLATE.format(slug=slug), json=greenhouse_payload)

    postings = GreenhouseAdapter().fetch(build_session("test/1.0"), slug)

    assert len(postings) == 1
    p = postings[0]
    assert p.source == "greenhouse"
    assert p.company == slug
    assert p.external_id == "1234567"
    assert p.title == "Analytics Engineer"
    assert p.location == "Ottawa, ON, Canada"
    assert p.remote_policy is None  # not exposed by Greenhouse in V1
    assert p.url.endswith("/1234567")
    assert p.posted_or_updated_at is not None
    assert p.raw["id"] == 1234567  # original item preserved
