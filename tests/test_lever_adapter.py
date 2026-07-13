import responses

from ingest.adapters.lever import LeverAdapter
from ingest.sources import LeverSource
from shared.http import build_session

URL_TEMPLATE = LeverSource(name="lever").url_template


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
