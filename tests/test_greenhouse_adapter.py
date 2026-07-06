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


@responses.activate
def test_greenhouse_unescapes_html_content(greenhouse_payload: dict) -> None:
    """The boards API ships `content` HTML-escaped; the adapter must emit real HTML.

    The fixture mirrors the live API: tags arrive as &lt;p&gt; and a literal
    ampersand arrives double-escaped (&amp;amp;). One unescape pass restores
    valid HTML while keeping in-HTML entities (&amp;) intact.
    """
    slug = "example"
    responses.add(responses.GET, URL_TEMPLATE.format(slug=slug), json=greenhouse_payload)

    (p,) = GreenhouseAdapter().fetch(build_session("test/1.0"), slug)

    assert p.description_html == (
        '<div class="content"><p>Build dbt models &amp; dashboards.</p></div>'
    )
    # the untouched escaped payload is still preserved in `raw` for debugging
    assert p.raw["content"].startswith("&lt;div")
