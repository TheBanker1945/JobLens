"""One career site on Workday: {tenant}.wd{N}.myworkdayjobs.com/{site}

Workday publishes no documented API for job seekers. Every career site is a
single-page app that asks its own server for JSON, and this adapter asks the
same two questions the page does:

- POST /wday/cxs/{tenant}/{site}/jobs -- a page of postings, 20 at most (a
  larger `limit` answers with an empty page), with `total` and the filters the
  page shows ("facets");
- GET /wday/cxs/{tenant}/{site}{externalPath} -- one posting's text.

Measured on Rabobank, 2026-09-23: 206 postings, a "Country" facet with "The
Netherlands" (61), and a text of 7,874 characters.

**Why robots.txt applies here, unlike to Recruitee or SmartRecruiters.** 5.1
made robots.txt the rule for what is *crawled* and a provider's documentation
the rule for a documented API. This is neither: it is the page's own plumbing,
like Indeed's app API. So the site owner's robots.txt decides, read for the
career site the JSON serves: a site is read only when robots.txt allows both its
pages (/{site}/) and its API path. Measured 2026-09-23: Rabobank disallows
/jobs/, its whole career site, and ING disallows /JVSGBLCOR/, the site its
Indeed links point at, while allowing four others with sitemaps. Reading their
JSON would go around a no that was written down, so neither is read. The
file is read with protego and the same RFC 9309 rules as every crawl (5.5): a
missing robots.txt allows everything, an unreadable one nothing.

**The order is the SmartRecruiters order.** The listing has title and place and
no text, so the Netherlands filter (a facet, when the site has one), the scope
and "already stored" all run before a text is asked for. A site without a
Netherlands facet lists the whole world; there a posting must name a Dutch place
before its text is requested.

**Closing jobs needs the whole listing.** Workday is a board: a stored job its
listing no longer holds has closed (sources/sightings.py). But this adapter
reads at most `max_postings` a run, and Workday is reported to stop paging near
2,000 results. A listing that stopped short says nothing about the jobs past the
point it stopped, so `stats.complete` is False and nothing on the board closes.
"""

import re
from dataclasses import dataclass
from datetime import datetime

import httpx
from protego import Protego

from joblens.sources.base import Vacancy
from joblens.sources.clean import redact, to_clean_text
from joblens.sources.http import RateLimited, get_json
from joblens.sources.polite import ROBOTS_AGENT, Disallowed
from joblens.sources.scope import Scope
from joblens.sources.scraped import MIN_TEXT_CHARS
from joblens.sources.smartrecruiters import BoardStats

PAGE = 20  # Workday's own page size, and its maximum
MAX_POSTINGS = 400  # per site per run: 20 requests; more is not a listing we read
NETHERLANDS = {"netherlands", "the netherlands", "nederland", "nld", "nl"}
LEDGER_CODE = re.compile(r"^[A-Za-z]*\d[A-Za-z0-9]*\s")  # "NL3M ", "411 "


@dataclass
class WorkdayStats(BoardStats):
    total: int = 0  # postings on the site, all countries
    dutch: int | None = None  # in the Netherlands, when the site can filter on it
    complete: bool = True  # False: the listing stopped short, so close nothing


def parse_board(board: str) -> tuple[str, str, str]:
    """ "rabobank.wd3/jobs" -> ("rabobank", "wd3", "jobs")."""
    host, site = board.split("/", 1)
    tenant, datacenter = host.rsplit(".", 1)
    return tenant, datacenter, site


class WorkdaySource:
    name = "workday"

    def __init__(
        self,
        board: str,
        client: httpx.Client,
        *,
        scope: Scope | None = None,
        known_keys: set[str] | frozenset[str] = frozenset(),
        max_postings: int = MAX_POSTINGS,
        robots: dict[str, Protego] | None = None,
    ):
        self.board = board  # which listing closes its jobs (sources/sightings.py)
        # Not `site`: the fetch script reads `site` as "a JobSpy source, gate it
        # per search" (scraped.py), and "jobs" is not a site to ask the gate for.
        self.tenant, self.datacenter, self.career_site = parse_board(board)
        self.host = f"{self.tenant}.{self.datacenter}.myworkdayjobs.com"
        self.api = f"https://{self.host}/wday/cxs/{self.tenant}/{self.career_site}"
        self.client = client
        self.scope = scope
        self.known_keys = known_keys
        self.max_postings = max_postings
        self.robots = robots if robots is not None else {}  # one read per host
        self.dutch_facet: tuple[str, str] | None = None
        self.stats = WorkdayStats()

    def check_robots(self) -> None:
        """Raise `Disallowed` unless robots.txt allows this career site."""
        parser = self.robots.get(self.host)
        if parser is None:
            response = self.client.get(f"https://{self.host}/robots.txt")
            # RFC 9309: a missing robots.txt (4xx) allows everything; one that
            # cannot be read (5xx) allows nothing, the same rule as 5.5's crawler.
            if response.status_code >= 500:
                raise Disallowed(
                    self.host, "robots.txt could not be read, so nothing is read"
                )
            parser = Protego.parse(response.text if response.status_code < 400 else "")
            self.robots[self.host] = parser
        pages = f"/{self.career_site}/"
        api = f"/wday/cxs/{self.tenant}/{self.career_site}/jobs"
        for path in (pages, api):
            if not parser.can_fetch(f"https://{self.host}{path}", ROBOTS_AGENT):
                raise Disallowed(
                    self.host, f"robots.txt disallows {path}, so the site is not read"
                )

    def listing(self) -> list[dict]:
        """The site's postings in the Netherlands: titles and places, no text.

        The first page tells which filter means "the Netherlands"; the rest are
        asked with it. `stats.complete` says whether this is all of them.
        """
        self.check_robots()
        first = self._page(0, {})
        self.stats.total = first.get("total", 0)
        self.dutch_facet = find_dutch_facet(first.get("facets", []))
        applied = {}
        page = first
        if self.dutch_facet:
            parameter, value = self.dutch_facet
            applied = {parameter: [value]}
            page = self._page(0, applied)
        total = page.get("total", 0)
        self.stats.dutch = total if self.dutch_facet else None
        postings = list(page.get("jobPostings", []))
        while len(postings) < min(total, self.max_postings):
            more = self._page(len(postings), applied).get("jobPostings", [])
            if not more:  # Workday's ceiling, or a listing that changed mid-read
                break
            postings.extend(more)
        self.stats.complete = len(postings) >= total
        return postings[: self.max_postings]

    def fetch(self, limit: int | None = None) -> list[Vacancy]:
        """The postings in scope that are not stored yet, at most `limit` of
        them: the limit counts text requests, the only ones that cost."""
        self.stats = WorkdayStats()
        postings = self.listing()
        self.stats.listed = len(postings)
        self.stats.listed_keys = {self.key(p["externalPath"]) for p in postings}
        vacancies: list[Vacancy] = []
        for posting in postings:
            if limit is not None and len(vacancies) >= limit:
                break
            title = posting.get("title", "")
            place = posting.get("locationsText", "")
            reason = self.not_wanted(title, place)
            if reason:
                self.stats.out_of_scope += 1
                self.stats.left_out.append(f"{title} -- {reason}")
                continue
            if self.key(posting["externalPath"]) in self.known_keys:
                self.stats.skipped_known += 1
                continue
            vacancy = self.vacancy(posting["externalPath"])
            if vacancy is not None:
                vacancies.append(vacancy)
        return vacancies

    def not_wanted(self, title: str, place: str) -> str:
        """Why a listed posting is not worth a text request, or ""."""
        if self.scope is None:
            return ""
        if not self.dutch_facet and not self.scope.places.find(place):
            return f"no Netherlands filter on this site, and no Dutch place: {place}"
        verdict = self.scope.check_listing(title, place)
        return "" if verdict.keep else verdict.reason

    def key(self, external_path: str) -> str:
        return f"{self.name}:{self.source_id(external_path)}"

    def source_id(self, external_path: str) -> str:
        """ "/job/Utrecht/Data-Engineer_JR_00145400-1" -> "rabobank:JR_00145400-1".

        The requisition id after the title, which survives a renamed title; the
        tenant in front, because two companies can number their jobs alike.
        """
        segment = external_path.rstrip("/").rsplit("/", 1)[-1]
        requisition = segment.split("_", 1)[1] if "_" in segment else segment
        return f"{self.tenant}:{requisition}"

    def vacancy(self, external_path: str) -> Vacancy | None:
        detail = get_json(self.client, f"{self.api}{external_path}", self.name)
        info = detail.get("jobPostingInfo") or {}
        if info.get("canApply") is False:
            self.stats.dropped_invalid += 1
            return None
        text = to_clean_text(info.get("jobDescription") or "")
        if len(text) < MIN_TEXT_CHARS:
            self.stats.dropped_no_text += 1
            return None
        country = ((info.get("country") or {}).get("descriptor") or "").lower()
        return Vacancy(
            source=self.name,
            source_id=self.source_id(external_path),
            url=info.get("externalUrl") or f"https://{self.host}/{self.career_site}",
            title=(info.get("title") or "").strip(),
            company=self.company(detail),
            city=info.get("location"),
            country="NL" if country in NETHERLANDS else None,
            posted_at=_parse_date(info.get("startDate")),
            text=text,
            raw=redact(info),
        )

    def company(self, detail: dict) -> str:
        """The employer's name, or the tenant's when Workday gives a ledger entry.

        `hiringOrganization` is the legal entity, and some tenants prefix it with
        an internal code: "411 SFDC Netherlands B.V.", "NL3M Philips
        International BV", "NL9A NL Best Industrial" (three Philips entities),
        "4990 Diversey EPC" for a Solenis job (2026-09-23). None of that is a name
        a person searches for or another board writes, and the duplicate check
        compares company names. So a name that starts with a code gives way to
        the tenant: "Philips", "Salesforce", "Solenis".
        """
        name = ((detail.get("hiringOrganization") or {}).get("name") or "").strip()
        if not name or LEDGER_CODE.match(name):
            return self.tenant.title()
        return name

    def _page(self, offset: int, applied: dict) -> dict:
        body = {"appliedFacets": applied, "limit": PAGE, "offset": offset}
        response = self.client.post(f"{self.api}/jobs", json={**body, "searchText": ""})
        if response.status_code == 429:
            raise RateLimited(self.name, None)
        response.raise_for_status()
        return response.json()


def find_dutch_facet(facets: list[dict]) -> tuple[str, str] | None:
    """The (facet, value id) that means "in the Netherlands", or None.

    Tenants name the facet differently ("Country", "locationCountry", a country
    inside a location group), so it is found by the value, not the name, and a
    facet whose name says "country" is preferred over one that merely has a
    value called Netherlands.
    """
    found: list[tuple[bool, str, str]] = []

    def walk(facet: dict) -> None:
        for value in facet.get("values", []):
            if "values" in value:  # a group: its members are facets themselves
                walk(value)
            elif (value.get("descriptor") or "").strip().lower() in NETHERLANDS:
                parameter = facet.get("facetParameter", "")
                found.append(("country" in parameter.lower(), parameter, value["id"]))

    for facet in facets:
        walk(facet)
    if not found:
        return None
    _, parameter, value = max(found, key=lambda item: item[0])
    return parameter, value


def _parse_date(value: str | None) -> datetime | None:
    try:  # "2026-09-23"
        return datetime.fromisoformat(value) if value else None
    except ValueError:
        return None
