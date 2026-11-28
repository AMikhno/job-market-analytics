"""The version lives in three files, and release-please only writes two of them.

pyproject.toml and .release-please-manifest.json are bumped together by the
release PR; uv.lock is synced by a follow-up step in the Release workflow. These
tests fail the build if any of the three drift apart, e.g. a hand edit to one.
"""

import json
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _pyproject_version() -> str:
    data = tomllib.loads((ROOT / "pyproject.toml").read_text())
    version = data["project"]["version"]
    assert isinstance(version, str)
    return version


def test_manifest_matches_pyproject() -> None:
    manifest = json.loads((ROOT / ".release-please-manifest.json").read_text())
    assert manifest == {".": _pyproject_version()}


def test_lockfile_records_the_pyproject_version() -> None:
    lock = tomllib.loads((ROOT / "uv.lock").read_text())
    name = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]["name"]
    own = [p for p in lock["package"] if p["name"] == name]
    assert len(own) == 1
    assert own[0]["version"] == _pyproject_version()


def test_config_releases_the_root_package_with_bare_tags() -> None:
    config = json.loads((ROOT / "release-please-config.json").read_text())
    root = config["packages"]["."]
    assert root["release-type"] == "python"
    # Existing tags are `v0.2.0`, not `job-market-analytics-v0.2.0`; a component
    # in the tag would make release-please miss the last release entirely.
    assert root["include-component-in-tag"] is False
