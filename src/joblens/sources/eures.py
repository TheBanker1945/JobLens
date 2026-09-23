"""EURES, the EU job portal -- and through it werk.nl, the Dutch public
employment service (UWV), which publishes no feed of its own.

**Public, but not documented.** This is the endpoint the europa.eu portal's own
pages call; no key, no login. The EU does not document it as an API (a third
party does: github.com/rorar/EURES-API-Documentation), so it can change without
notice, like Indeed's app API in scraped.py. europa.eu's robots.txt asks for
`Crawl-delay: 10`; sources.toml gives the gate those ten seconds for this site.

What was measured before a line was written (2026-09-23, 13 requests):

- **Regions are NUTS 2024 codes.** Utrecht is NL35 (it was NL31) and
  Zuid-Holland NL36 (it was NL33); Noord-Holland NL32 and Zeeland NL34 did not
  change. The old codes answer with nothing at all: `nl33` returned 0 records,
  and a first probe with `nl31`-`nl34` silently missed Utrecht and Zuid-Holland.
  NL350 is IJsselstein, NL366 Rotterdam. So the place half of the scope happens
  at EURES, before a record arrives; the code is also written into the location
  as a province name, so the scope and the reader can see it.
- **Title matching, not everywhere.** "developer" anywhere in the text gave 666
  records in the four provinces, starting with a Georgian restaurant's content
  specialist and a tech recruiter; in the title, 115.
- **Language.** Records from the Dutch feed say `nl` even when written in
  English; German cross-border records say `de` (34 of 50 on one Dutch page).
  Kept: `nl` and `en`.
- **The text is a summary.** Descriptions stop near 2,000 characters in the
  middle of a sentence ("... IT implementation & roll..."), in the search result
  and in the detail alike. A detail request would add only the contact persons
  (names, addresses), which we do not want. So there are none: one search is one
  request, and everything stored comes from it.
- **"Last week" is the last change, not the creation.** `publicationPeriod`
  LAST_WEEK returned vacancies created in June. `creationDate` (epoch ms) is
  checked here against `max_age_days`.
"""

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import httpx

from joblens.sources.base import Vacancy
from joblens.sources.clean import redact, to_clean_text
from joblens.sources.scraped import MIN_TEXT_CHARS

SEARCH = "https://europa.eu/eures/api/jv-searchengine/public/jv-search/search"
PORTAL = "https://europa.eu/eures/portal/jv-se/jv-details/{id}?lang=en"
PAGE = 50  # the API's maximum
LANGUAGES = frozenset({"nl", "en"})
# NUTS 2024, level 2: the codes EURES files a Dutch vacancy under.
NUTS_PROVINCES: Mapping[str, str] = {
    "NL11": "Groningen",
    "NL12": "Fryslân",
    "NL13": "Drenthe",
    "NL21": "Overijssel",
    "NL22": "Gelderland",
    "NL23": "Flevoland",
    "NL32": "Noord-Holland",
    "NL34": "Zeeland",
    "NL35": "Utrecht",
    "NL36": "Zuid-Holland",
    "NL41": "Noord-Brabant",
    "NL42": "Limburg",
}
# Never stored, even if EURES starts sending them with a search result: the
# names and addresses of contact persons, which the detail record carries.
PERSONAL = ("personContacts", "applicationInstructions")


def nuts_codes(provinces: Iterable[str]) -> list[str]:
    """["Utrecht", "Zeeland"] -> ["nl34", "nl35"]: the scope, as EURES reads it."""
    by_name = {name: code.lower() for code, name in NUTS_PROVINCES.items()}
    unknown = [name for name in provinces if name not in by_name]
    if unknown:
        raise ValueError(f"no NUTS code for: {', '.join(unknown)}")
    return sorted(by_name[name] for name in provinces)


def province_of(code: str | None) -> str | None:
    """ "NL366" -> "Zuid-Holland"; None for a code EURES left empty."""
    return NUTS_PROVINCES.get((code or "")[:4].upper())


@dataclass
class SearchStats:
    listed: int = 0  # records the search returned
    dropped_language: int = 0  # not Dutch or English
    dropped_old: int = 0  # created longer ago than max_age_days
    dropped_no_text: int = 0

    @property
    def dropped_invalid(self) -> int:
        """What the run report calls unusable: the wrong language, or too old."""
        return self.dropped_language + self.dropped_old


def search_body(keyword: str, locations: list[str], page: int) -> dict:
    """The body the portal sends, with the filters we use filled in."""
    return {
        "resultsPerPage": PAGE,
        "page": page,
        "sortSearch": "MOST_RECENT",
        "keywords": [{"keyword": keyword, "specificSearchCode": "TITLE"}],
        "publicationPeriod": "LAST_WEEK",
        "occupationUris": [],
        "skillUris": [],
        "requiredExperienceCodes": [],
        "positionScheduleCodes": [],
        "sectorCodes": [],
        "educationAndQualificationLevelCodes": [],
        "positionOfferingCodes": [],
        "locationCodes": locations,
        "euresFlagCodes": [],
        "otherBenefitsCodes": [],
        "requiredLanguages": [],
        "minNumberPost": None,
        "sessionId": "joblens",
        "requestLanguage": "en",
    }


class EuresSource:
    """One keyword, searched in job titles, in the chosen regions."""

    name = "eures"

    def __init__(
        self,
        client: httpx.Client,
        *,
        keyword: str,
        locations: list[str],
        max_age_days: int = 60,
        pages: int = 2,
        now: datetime | None = None,
    ):
        self.client = client
        self.keyword = keyword
        self.locations = locations
        self.max_age = timedelta(days=max_age_days)
        self.pages = pages
        self.now = now
        self.stats = SearchStats()

    def fetch(self, limit: int | None = None) -> list[Vacancy]:
        self.stats = SearchStats()
        now = self.now or datetime.now(UTC)
        pages = self.pages if limit is None else min(self.pages, -(-limit // PAGE))
        vacancies: list[Vacancy] = []
        for page in range(1, pages + 1):
            response = self.client.post(
                SEARCH, json=search_body(self.keyword, self.locations, page)
            )
            response.raise_for_status()  # a 429 is raised by the gate before this
            records = response.json().get("jvs", [])
            self.stats.listed += len(records)
            for record in records:
                vacancy = self.vacancy(record, now)
                if vacancy is not None:
                    vacancies.append(vacancy)
            if len(records) < PAGE:
                break
        return vacancies[:limit]

    def vacancy(self, record: dict, now: datetime) -> Vacancy | None:
        languages = set(record.get("availableLanguages") or [])
        if languages and not languages & LANGUAGES:
            self.stats.dropped_language += 1
            return None
        created = datetime.fromtimestamp(record["creationDate"] / 1000, UTC)
        if now - created > self.max_age:
            self.stats.dropped_old += 1
            return None
        text = to_clean_text(record.get("description") or "")
        if len(text) < MIN_TEXT_CHARS:
            self.stats.dropped_no_text += 1
            return None
        codes = [c for cs in (record.get("locationMap") or {}).values() for c in cs]
        provinces = sorted({p for code in codes if (p := province_of(code))})
        kept = {
            key: value
            for key, value in record.items()
            if key not in ("description", "translations", *PERSONAL)
        }
        return Vacancy(
            source=self.name,
            source_id=str(record["id"]),
            url=PORTAL.format(id=record["id"]),
            title=(record.get("title") or "").strip(),
            company=(record.get("employer") or {}).get("name"),
            country="NL",
            posted_at=created,
            text=text,
            # the province, in words, where the scope and a person can read it
            raw=redact({**kept, "location": ", ".join([*provinces, "Netherlands"])}),
        )
