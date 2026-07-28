import pytest
import requests
import responses

from ingest.adapters.lever import LeverAdapter
from ingest.sources import LeverSource
from shared.http import build_session

URL_TEMPLATE = LeverSource(name="lever").url_template
EU_URL_TEMPLATE = LeverSource(name="lever").eu_url_template


@responses.activate
def test_lever_maps_and_assembles_body(lever_payload: list) -> None:
    board_ref = "example"
    responses.add(responses.GET, URL_TEMPLATE.format(board_ref=board_ref), json=lever_payload)

    postings = LeverAdapter(URL_TEMPLATE).fetch(build_session("test/1.0"), board_ref)

    assert len(postings) == 1
    p = postings[0]
    assert p.source == "lever"
    assert p.external_id == "abc-123"
    assert p.title == "Data Engineer"
    assert p.remote_policy == "remote"
    assert p.employment_type == "Full-time"
    # body is assembled from description + lists + additional
    assert "Own the data platform" in p.description_html
    assert "Kafka and Spark" in p.description_html
    assert "Airflow" in p.description_html
    assert p.posted_or_updated_at is not None


@responses.activate
def test_lever_non_array_response_raises() -> None:
    """Lever returns a bare JSON array; a dict is an error body / schema
    drift and must raise (per-company warn), not land 0 rows."""
    board_ref = "example"
    responses.add(
        responses.GET, URL_TEMPLATE.format(board_ref=board_ref), json={"error": "no site"}
    )

    with pytest.raises(ValueError, match="expected a JSON array"):
        LeverAdapter(URL_TEMPLATE).fetch(build_session("test/1.0"), board_ref)


@responses.activate
def test_lever_falls_back_to_the_eu_shard_on_404(lever_payload: list) -> None:
    """Some boards live only on api.eu.lever.co and the US host 404s for them
    (REDACTED is a live example). Region is not a company property, so the adapter
    retries the EU shard instead of the list carrying one."""
    board_ref = "example"
    responses.add(responses.GET, URL_TEMPLATE.format(board_ref=board_ref), status=404)
    responses.add(responses.GET, EU_URL_TEMPLATE.format(board_ref=board_ref), json=lever_payload)

    postings = LeverAdapter(URL_TEMPLATE, EU_URL_TEMPLATE).fetch(
        build_session("test/1.0"), board_ref
    )

    assert len(postings) == 1
    assert postings[0].title == "Data Engineer"


@responses.activate
def test_lever_raises_when_both_shards_404() -> None:
    """A board missing from both shards is genuinely gone: the fallback must not
    turn a dead board into a silent success."""
    board_ref = "example"
    responses.add(responses.GET, URL_TEMPLATE.format(board_ref=board_ref), status=404)
    responses.add(responses.GET, EU_URL_TEMPLATE.format(board_ref=board_ref), status=404)

    with pytest.raises(requests.HTTPError):
        LeverAdapter(URL_TEMPLATE, EU_URL_TEMPLATE).fetch(build_session("test/1.0"), board_ref)


@responses.activate
def test_lever_does_not_retry_non_404_errors() -> None:
    """Only a 404 means "not on this shard". Any other error must propagate from
    the first call, not be masked by an EU retry. 403 is used rather than 500
    because the session retries 5xx itself (shared/http.py status_forcelist),
    which would exercise urllib3's retry loop instead of this fallback."""
    board_ref = "example"
    responses.add(responses.GET, URL_TEMPLATE.format(board_ref=board_ref), status=403)

    with pytest.raises(requests.HTTPError):
        LeverAdapter(URL_TEMPLATE, EU_URL_TEMPLATE).fetch(build_session("test/1.0"), board_ref)
    # the EU shard was never called
    assert all("api.eu.lever.co" not in c.request.url for c in responses.calls)
