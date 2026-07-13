import responses

from ingest.adapters.greenhouse import GreenhouseAdapter
from ingest.sources import GreenhouseSource
from shared.http import build_session

URL_TEMPLATE = GreenhouseSource(name="greenhouse").url_template


def _adapter() -> GreenhouseAdapter:
    return GreenhouseAdapter(URL_TEMPLATE)


@responses.activate
def test_greenhouse_maps_common_schema(greenhouse_payload: dict) -> None:
    board_ref = "example"
    responses.add(responses.GET, URL_TEMPLATE.format(board_ref=board_ref), json=greenhouse_payload)

    postings = _adapter().fetch(build_session("test/1.0"), board_ref)

    assert len(postings) == 1
    p = postings[0]
    assert p.source == "greenhouse"
    assert p.company == board_ref
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
    board_ref = "example"
    responses.add(responses.GET, URL_TEMPLATE.format(board_ref=board_ref), json=greenhouse_payload)

    (p,) = _adapter().fetch(build_session("test/1.0"), board_ref)

    assert p.description_html == (
        '<div class="content"><p>Build dbt models &amp; dashboards.</p></div>'
    )
    # the untouched escaped payload is still preserved in `raw` for debugging
    assert p.raw["content"].startswith("&lt;div")
