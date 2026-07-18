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
