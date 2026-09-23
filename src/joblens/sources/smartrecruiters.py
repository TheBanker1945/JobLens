"""One company's postings on SmartRecruiters: api.smartrecruiters.com/v1/companies/{id}

The public Posting API: documented, no key. (Its robots.txt disallows everything
for crawlers; see sources/polite.py for why a documented API is not a crawl.)

Different in one way that matters from Recruitee and Greenhouse: the listing
has titles and places but no text. The text is one more request *per vacancy*.
So the order is everything here, the same lesson as the board cap and the scope:

1. the listing, Dutch only (`country=nl`), 100 per page;
2. the scope, on title and place from the listing;
3. anything already stored is skipped;
4. only then a request for the text, for what is left.

Measured 2026-09-23 on Varrlyn: the listing says "Amsterdam, NH, Netherlands"
and a detail carries four sections -- company, job description, qualifications,
additional information -- as HTML.
"""

from dataclasses import dataclass, field
from datetime import datetime

import httpx

from joblens.sources.base import Vacancy
from joblens.sources.clean import redact, to_clean_text
from joblens.sources.http import get_json
from joblens.sources.scope import Scope
from joblens.sources.scraped import MIN_TEXT_CHARS

API = "https://api.smartrecruiters.com/v1/companies/{company}/postings"
PAGE = 100  # the API's maximum
SECTIONS = (
    "companyDescription",
    "jobDescription",
    "qualifications",
    "additionalInformation",
)


@dataclass
class BoardStats:
    """What the listing held, and what was never asked for and why."""

    listed: int = 0  # Dutch postings in the listing
    out_of_scope: int = 0  # never asked for: outside the scope
    skipped_known: int = 0  # never asked for: already stored
    dropped_invalid: int = 0  # asked for, and no longer active or no text
    dropped_no_text: int = 0
    left_out: list[str] = field(default_factory=list)


class SmartRecruitersSource:
    name = "smartrecruiters"

    def __init__(
        self,
        company: str,
        client: httpx.Client,
        *,
        scope: Scope | None = None,
        known_keys: set[str] | frozenset[str] = frozenset(),
    ):
        self.company = company
        self.client = client
        self.scope = scope
        self.known_keys = known_keys
        self.stats = BoardStats()

    def listing(self) -> list[dict]:
        """Every Dutch posting of this company: titles and places, no text."""
        postings: list[dict] = []
        while True:
            params = {"country": "nl", "limit": PAGE, "offset": len(postings)}
            page = get_json(
                self.client, API.format(company=self.company), self.name, params
            )
            content = page.get("content", [])
            postings.extend(content)
            if not content or len(postings) >= page.get("totalFound", 0):
                return postings

    def fetch(self, limit: int | None = None) -> list[Vacancy]:
        """The vacancies in scope that are not stored yet, at most `limit` of
        them: the limit counts text requests, the only ones that cost."""
        self.stats = BoardStats()
        postings = self.listing()
        self.stats.listed = len(postings)
        vacancies: list[Vacancy] = []
        for posting in postings:
            if limit is not None and len(vacancies) >= limit:
                break
            title = posting.get("name", "")
            if self.scope is not None:
                place = (posting.get("location") or {}).get("fullLocation", "")
                verdict = self.scope.check_listing(title, place)
                if not verdict.keep:
                    self.stats.out_of_scope += 1
                    self.stats.left_out.append(f"{title} -- {verdict.reason}")
                    continue
            if f"{self.name}:{posting['id']}" in self.known_keys:
                self.stats.skipped_known += 1
                continue
            vacancy = self.vacancy(posting["id"])
            if vacancy is not None:
                vacancies.append(vacancy)
        return vacancies

    def vacancy(self, posting_id: str) -> Vacancy | None:
        url = f"{API.format(company=self.company)}/{posting_id}"
        detail = get_json(self.client, url, self.name)
        if detail.get("active") is False:
            self.stats.dropped_invalid += 1
            return None
        sections = (detail.get("jobAd") or {}).get("sections") or {}
        html = "\n".join(
            f"<h2>{part.get('title', '')}</h2>{part.get('text', '')}"
            for name in SECTIONS
            if (part := sections.get(name)) and part.get("text")
        )
        text = to_clean_text(html)
        # The same floor as the scraped sources. Deloitte posts events as jobs:
        # "Engineering, AI & Data Kookworkshop" came with 131 characters, one
        # sentence and an "x" (2026-09-23).
        if len(text) < MIN_TEXT_CHARS:
            self.stats.dropped_no_text += 1
            return None
        location = detail.get("location") or {}
        return Vacancy(
            source=self.name,
            source_id=str(detail["id"]),
            url=detail.get("postingUrl") or url,
            title=(detail.get("name") or "").strip(),
            company=(detail.get("company") or {}).get("name") or self.company,
            city=location.get("city"),
            country=(location.get("country") or "").upper() or None,
            posted_at=_parse_date(detail.get("releasedDate")),
            text=text,
            raw=redact(detail),
        )


def _parse_date(value: str | None) -> datetime | None:
    try:  # "2026-09-23T07:56:24.915Z"
        return datetime.fromisoformat(value.replace("Z", "+00:00")) if value else None
    except ValueError:
        return None
