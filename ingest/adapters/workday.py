"""Workday careers-site adapter (public "cxs" API, no auth).

POST https://{tenant}.{shard}.myworkdayjobs.com/wday/cxs/{tenant}/{site}/jobs
    body {"appliedFacets": {...}, "limit": 20, "offset": N, "searchText": ""}
    -> {"total": M, "jobPostings": [...], "facets": [...]}
GET  https://{tenant}.{shard}.myworkdayjobs.com/wday/cxs/{tenant}/{site}{externalPath}
    -> {"jobPostingInfo": {"jobDescription": …, "location": …,
                           "additionalLocations": [...], "externalUrl": …, …}}

`board_ref` is `tenant/shard/site` (ADR-0012) -- the one source whose ref is
several segments, so it is split here rather than substituted whole.

Three properties of this API shape the adapter (ADR-0030):

* **The list is filtered by location on Workday's side.** An unfiltered list
  stops at 2000 postings, and a large board is far bigger than that. The
  board's own facets (returned with every list response) carry an id per
  country and per location type; the configured rules are resolved against
  those ids by name, and only matching postings are ever listed. Different
  facets AND together and values within one facet OR, so each rule is its own
  query; a multi-location posting can match two and is kept once.
* **`total` is only reported on the first page.** Later pages say 0, so the
  walk keeps the first page's figure. Re-reading it per page stops after two.
* **Only new postings cost a detail call.** The list carries just title,
  path, a location summary that may be a count ("5 Locations") and relative
  date text, so the description, real locations, date and public URL all come
  from the detail. A posting already landed with a description is passed in as
  `described` and lands from the list alone; bronze carries its detail fields
  forward (stg_workday__jobs). After the first run, cost follows new postings,
  not board size.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import requests

from shared.http import FetchPolicy
from shared.models import RawPosting, WorkdayLocationRule
from shared.redact import redact_ref

log = logging.getLogger(__name__)

# The list's hard ceiling: past this many postings Workday returns no more,
# whatever the offset. Hitting it means the filters are too broad for the board.
LIST_CAP = 2000

# locationsText is a count, not a place, for a multi-location posting.
_LOCATION_COUNT = re.compile(r"^\d+ Locations?$")

# board_ref -> externalPaths already landed with a description.
Described = Mapping[str, frozenset[str]]


@dataclass(frozen=True)
class WorkdayRef:
    """A parsed `tenant/shard/site` board_ref."""

    tenant: str
    shard: str
    site: str

    @classmethod
    def parse(cls, board_ref: str) -> WorkdayRef:
        parts = board_ref.split("/")
        if len(parts) != 3 or not all(parts):
            raise ValueError(f"expected tenant/shard/site, got {len(parts)} segment(s)")
        return cls(*parts)

    @property
    def host(self) -> str:
        return f"{self.tenant}.{self.shard}.myworkdayjobs.com"

    def fill(self, template: str, **extra: str) -> str:
        return template.format(tenant=self.tenant, shard=self.shard, site=self.site, **extra)


class WorkdayAdapter:
    source = "workday"

    def __init__(
        self,
        url_template: str,
        detail_url_template: str,
        public_url_template: str,
        policy: FetchPolicy | None = None,
        *,
        locations: Sequence[WorkdayLocationRule] = (),
        described: Described | None = None,
        page_size: int = 20,
    ) -> None:
        self.url_template = url_template
        self.detail_url_template = detail_url_template
        self.public_url_template = public_url_template
        self.policy = policy or FetchPolicy()
        self.locations = tuple(locations)
        # Read-only and shared by every worker thread; never mutated here.
        self.described: Described = described or {}
        self.page_size = page_size

    def fetch(self, session: requests.Session, board_ref: str) -> list[RawPosting]:
        if not self.locations:
            # Without rules the only alternative is the unfiltered board: up to
            # 2000 detail calls for one company. Refuse loudly instead.
            raise ValueError("no Workday location rules configured (WORKDAY_LOCATIONS is empty)")
        ref = WorkdayRef.parse(board_ref)
        url = ref.fill(self.url_template)

        # The facets come back with any list response; one unfiltered row is
        # the cheapest way to learn this board's ids.
        facets = self._page(session, url, {}, offset=0, limit=1)["facets"]
        queries = _resolve(facets, self.locations)
        if queries is None:
            raise LookupError(
                "none of the configured countries is a facet on this board "
                "(no postings there, or a facet layout this adapter does not read)"
            )

        listings: dict[str, dict[str, Any]] = {}
        for applied in queries:
            for item in self._walk(session, url, applied, board_ref):
                listings.setdefault(item["externalPath"], item)

        known = self.described.get(board_ref, frozenset())
        return [
            self._map_listed(item, ref, board_ref)
            if path in known
            else self._map_detailed(item, self._detail(session, ref, path), ref, board_ref)
            for path, item in listings.items()
        ]

    def _page(
        self,
        session: requests.Session,
        url: str,
        applied: dict[str, list[str]],
        *,
        offset: int,
        limit: int,
    ) -> dict[str, Any]:
        body = {"appliedFacets": applied, "limit": limit, "offset": offset, "searchText": ""}
        payload: dict[str, Any] = self.policy.post_json(session, url, body)
        return payload

    def _walk(
        self,
        session: requests.Session,
        url: str,
        applied: dict[str, list[str]],
        board_ref: str,
    ) -> list[dict[str, Any]]:
        """Every posting matching one query, page by page.

        Bounded by the first page's `total` and by an empty page, so a board
        that misreports its count cannot keep the walk going.
        """
        collected: list[dict[str, Any]] = []
        total: int | None = None
        while True:
            payload = self._page(session, url, applied, offset=len(collected), limit=self.page_size)
            # Strict: no "jobPostings" is schema drift or an error body.
            page: list[dict[str, Any]] = payload["jobPostings"]
            if total is None:
                total = int(payload["total"])  # only page 1 reports it
                if total >= LIST_CAP:
                    log.warning(
                        "workday board %s matches %d postings, the list cap: "
                        "postings past it cannot be fetched",
                        redact_ref(board_ref),
                        total,
                    )
            if not page:
                break
            collected.extend(page)
            if len(collected) >= total:
                break
        return collected

    def _detail(self, session: requests.Session, ref: WorkdayRef, path: str) -> dict[str, Any]:
        url = ref.fill(self.detail_url_template, external_path=path)
        info: dict[str, Any] = self.policy.get_json(session, url)["jobPostingInfo"]
        return info

    def _map_detailed(
        self, item: dict[str, Any], info: dict[str, Any], ref: WorkdayRef, board_ref: str
    ) -> RawPosting:
        places = _locations(info)
        return RawPosting(
            source=self.source,
            company=board_ref,
            external_id=item["externalPath"],
            title=item["title"],
            location="; ".join(places) or _listed_location(item),
            remote_policy="Remote" if any(_is_remote(p) for p in places) else None,
            employment_type=info.get("timeType"),
            url=info.get("externalUrl")
            or ref.fill(self.public_url_template, external_path=item["externalPath"]),
            # Strict (ADR-0021): a detail without a description is drift, and
            # landing the posting without its text would make it unrankable.
            description_html=info["jobDescription"],
            posted_or_updated_at=_parse_date(info.get("startDate")),
            raw={"listing": item, "detail": info},
        )

    def _map_listed(self, item: dict[str, Any], ref: WorkdayRef, board_ref: str) -> RawPosting:
        """A posting already landed with a description: list fields only.

        Detail-only columns are left empty on purpose -- bronze fills them from
        the last described landing, so an empty value here never reaches silver.
        """
        return RawPosting(
            source=self.source,
            company=board_ref,
            external_id=item["externalPath"],
            title=item["title"],
            location=_listed_location(item),
            url=ref.fill(self.public_url_template, external_path=item["externalPath"]),
            description_html="",
            raw={"listing": item},
        )


def _facet_groups(facets: Iterable[dict[str, Any]]) -> Iterable[tuple[str, list[dict[str, Any]]]]:
    """Every (facetParameter, leaf values) pair, flattening nested groups.

    Location facets nest: one parent holds the country, location-type and site
    groups as values of its own.
    """
    for facet in facets:
        values = facet.get("values") or []
        leaves = [v for v in values if "values" not in v]
        if leaves and facet.get("facetParameter"):
            yield facet["facetParameter"], leaves
        yield from _facet_groups(v for v in values if "values" in v)


def _match(
    facets: Sequence[dict[str, Any]], names: Sequence[str], skip: str | None = None
) -> tuple[str, list[str]] | None:
    """The first facet with a value named in `names`, and every matching id."""
    wanted = {n.casefold() for n in names}
    for param, leaves in _facet_groups(facets):
        if param == skip:
            continue
        ids = [v["id"] for v in leaves if str(v.get("descriptor", "")).casefold() in wanted]
        if ids:
            return param, ids
    return None


def _resolve(
    facets: Sequence[dict[str, Any]], rules: Sequence[WorkdayLocationRule]
) -> list[dict[str, list[str]]] | None:
    """One appliedFacets query per rule this board can answer.

    Facets list only values that have postings, so a missing country or type
    means zero postings for that rule, and the rule is dropped -- never widened
    to the unfiltered board. None means no configured country matched at all,
    which the caller treats as an error rather than an empty board.
    """
    queries: list[dict[str, list[str]]] = []
    any_country = False
    for rule in rules:
        country = _match(facets, rule.countries)
        if country is None:
            continue
        any_country = True
        applied = {country[0]: country[1]}
        if rule.location_types:
            kind = _match(facets, rule.location_types, skip=country[0])
            if kind is None:
                continue
            applied[kind[0]] = kind[1]
        queries.append(applied)
    return queries if any_country else None


def _locations(info: dict[str, Any]) -> list[str]:
    """Primary then additional locations, de-duplicated, in Workday's order.

    All of them, joined: a posting is kept if *any* of its places is allowed,
    and silver's location rule word-matches the whole field.
    """
    places = [info.get("location"), *(info.get("additionalLocations") or [])]
    return list(dict.fromkeys(p.strip() for p in places if p and p.strip()))


def _listed_location(item: dict[str, Any]) -> str | None:
    text = (item.get("locationsText") or "").strip()
    return None if not text or _LOCATION_COUNT.match(text) else text


def _is_remote(place: str) -> bool:
    # Workday spells a remote seat as a location segment: "Region, Remote".
    return any(part.strip().casefold() == "remote" for part in place.split(","))


def _parse_date(value: str | None) -> datetime | None:
    if not value:
        return None
    parsed = datetime.fromisoformat(value)  # startDate is a bare date: "2026-10-30"
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
