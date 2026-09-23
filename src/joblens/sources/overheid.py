"""Government vacancies: werkenbijdeoverheid.nl, read through its sitemap.

The Dutch government publishes its vacancies on werkenbijdeoverheid.nl, with a
sitemap that lists every one of them (1,394 on 2026-09-23, a superset of the
1,192 on werkenvoornederland.nl). It has no API, so this is the first source
JobLens *crawls*: the sitemap and the pages it lists are web pages, and every
request is marked `CRAWL` so the gate checks robots.txt first. The site allows
everything, with `Request-rate: 10/1`; the gate's own 1.5 s is slower still.

The order, once more, is the point:

1. **The sitemap**, one request: every vacancy URL. The URL ends in the
   vacancy's id ("...-DEF2660-2008-5412") and starts with its title in kebab
   case ("senior-python-engineer"), so the sitemap alone is a complete listing
   -- what closes a job when it disappears (sources/sightings.py) -- and a
   title list.
2. **The scope on that title**, before any page is asked for: 116 of 1,394 were
   software, data or AI work on 2026-09-23. The place is not in the URL, so it
   is checked once the page is read.
3. **Stored ones are skipped.** Then one request per page that is left.

A page carries its facts in an analytics data layer (`window.dataLayer.push`):
Functienaam, Rijksorganisatie, Standplaats, Startdatum, Einddatum. There is no
JSON-LD on this site (werkenvoornederland.nl has it, with a 104-character
teaser as the description). The text is the page's six sections, "Dit ga je
doen" to "Bijzonderheden", headings included.
"""

import re
import xml.etree.ElementTree as ElementTree
from dataclasses import dataclass, field
from datetime import datetime

import httpx

from joblens.sources.base import Vacancy
from joblens.sources.clean import extract_elements, redact, to_clean_text
from joblens.sources.polite import CRAWL
from joblens.sources.scope import Scope
from joblens.sources.scraped import MIN_TEXT_CHARS

SITEMAP = "https://www.werkenbijdeoverheid.nl/sitemap-vacatures.xml"
NAMESPACE = {"s": "http://www.sitemaps.org/schemas/sitemap/0.9"}
# "...-senior-python-engineer-DEF2660-2008-5412": organisation code, year, number
VACANCY_ID = re.compile(r"-([A-Za-z0-9]+-\d{4}-\d+)/?$")
DATA_LAYER = re.compile(r"dataLayer\.push\(\{(.*?)\}\)", re.S)
FIELD = re.compile(r"'([\w-]+)'\s*:\s*'([^']*)'")
MONTHS = {
    name: number
    for number, name in enumerate(
        (
            "januari februari maart april mei juni juli augustus september "
            "oktober november december"
        ).split(),
        start=1,
    )
}


@dataclass(frozen=True)
class Listed:
    url: str
    vacancy_id: str
    title: str  # from the URL: lower case, no punctuation, good enough for scope


@dataclass
class SitemapStats:
    listed: int = 0
    out_of_scope: int = 0  # never asked for: the title is not the work
    skipped_known: int = 0
    dropped_invalid: int = 0  # the page went, or said nothing we could read
    dropped_no_text: int = 0
    left_out: list[str] = field(default_factory=list)
    listed_keys: set[str] = field(default_factory=set)


class OverheidSource:
    name = "overheid"
    board = "werkenbijdeoverheid.nl"  # one board: the sitemap closes its jobs

    def __init__(
        self,
        client: httpx.Client,
        *,
        sitemap: str = SITEMAP,
        scope: Scope | None = None,
        known_keys: set[str] | frozenset[str] = frozenset(),
    ):
        self.client = client
        self.sitemap = sitemap
        self.scope = scope
        self.known_keys = known_keys
        self.stats = SitemapStats()

    def listing(self) -> list[Listed]:
        response = self.client.get(self.sitemap, extensions=CRAWL)
        response.raise_for_status()
        root = ElementTree.fromstring(response.content)
        listed = []
        for loc in root.iterfind("s:url/s:loc", NAMESPACE):
            url = (loc.text or "").strip()
            match = VACANCY_ID.search(url)
            if not match:
                continue
            head = url[: match.start()]  # ".../vacatures/senior-python-engineer"
            title = head.rsplit("/", 1)[-1].replace("-", " ")
            listed.append(Listed(url, match.group(1), title))
        return listed

    def fetch(self, limit: int | None = None) -> list[Vacancy]:
        """New vacancies in scope, at most `limit` pages: the pages are what cost."""
        self.stats = SitemapStats()
        listed = self.listing()
        self.stats.listed = len(listed)
        self.stats.listed_keys = {f"{self.name}:{item.vacancy_id}" for item in listed}
        vacancies: list[Vacancy] = []
        for item in listed:
            if limit is not None and len(vacancies) >= limit:
                break
            if self.scope is not None:
                verdict = self.scope.what(item.title)
                if not verdict.keep:
                    self.stats.out_of_scope += 1
                    self.stats.left_out.append(f"{item.title} -- {verdict.reason}")
                    continue
            if f"{self.name}:{item.vacancy_id}" in self.known_keys:
                self.stats.skipped_known += 1
                continue
            vacancy = self.vacancy(item)
            if vacancy is not None:
                vacancies.append(vacancy)
        return vacancies

    def vacancy(self, item: Listed) -> Vacancy | None:
        response = self.client.get(item.url, extensions=CRAWL)
        if response.status_code == 404:  # gone since the sitemap was written
            self.stats.dropped_invalid += 1
            return None
        response.raise_for_status()
        page = response.text
        facts = data_layer(page)
        sections = extract_elements(
            page, lambda attrs: (attrs.get("id") or "").endswith("_anchor")
        )
        text = to_clean_text("".join(sections))
        if len(text) < MIN_TEXT_CHARS:
            self.stats.dropped_no_text += 1
            return None
        return Vacancy(
            source=self.name,
            source_id=item.vacancy_id,
            url=item.url,
            title=facts.get("Functienaam") or item.title,
            company=facts.get("Rijksorganisatie"),
            city=facts.get("Standplaats"),
            country="NL",
            posted_at=dutch_date(facts.get("Startdatum")),
            text=text,
            raw=redact({"url": item.url, **facts}),
        )


def data_layer(page: str) -> dict[str, str]:
    """The fields of the page's `dataLayer.push({...})`, as strings."""
    block = DATA_LAYER.search(page)
    if not block:
        return {}
    return {key: value.strip() for key, value in FIELD.findall(block.group(1))}


def dutch_date(value: str | None) -> datetime | None:
    """ "4 september 2026" -> 2026-09-04."""
    parts = (value or "").lower().split()
    if len(parts) != 3 or parts[1] not in MONTHS:
        return None
    try:
        return datetime(int(parts[2]), MONTHS[parts[1]], int(parts[0]))
    except ValueError:
        return None
