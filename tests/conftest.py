import json
from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def greenhouse_payload() -> dict:
    return json.loads((FIXTURES / "greenhouse_jobs.json").read_text())


@pytest.fixture
def lever_payload() -> list:
    return json.loads((FIXTURES / "lever_postings.json").read_text())


@pytest.fixture
def ashby_payload() -> dict:
    return json.loads((FIXTURES / "ashby_jobs.json").read_text())


@pytest.fixture
def bamboohr_list_payload() -> dict:
    return json.loads((FIXTURES / "bamboohr_list.json").read_text())


@pytest.fixture
def bamboohr_detail_payload() -> dict:
    return json.loads((FIXTURES / "bamboohr_detail.json").read_text())


@pytest.fixture
def recruitee_payload() -> dict:
    return json.loads((FIXTURES / "recruitee_offers.json").read_text())


@pytest.fixture
def workable_payload() -> dict:
    return json.loads((FIXTURES / "workable_account.json").read_text())


@pytest.fixture
def pinpoint_payload() -> dict:
    return json.loads((FIXTURES / "pinpoint_postings.json").read_text())


@pytest.fixture
def rippling_payload() -> list:
    return json.loads((FIXTURES / "rippling_jobs.json").read_text())


@pytest.fixture
def rippling_detail_payload() -> dict:
    return json.loads((FIXTURES / "rippling_job_detail.json").read_text())


@pytest.fixture
def smartrecruiters_payload() -> dict:
    return json.loads((FIXTURES / "smartrecruiters_postings.json").read_text())


@pytest.fixture
def smartrecruiters_page2_payload() -> dict:
    return json.loads((FIXTURES / "smartrecruiters_postings_page2.json").read_text())


@pytest.fixture
def smartrecruiters_detail_payload() -> dict:
    return json.loads((FIXTURES / "smartrecruiters_posting_detail.json").read_text())
