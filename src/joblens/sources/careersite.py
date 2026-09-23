"""An employer's own career site, read through its sitemap and its JobPostings.

Many Dutch employers run a "werkenbij..." site on a system no adapter names.
What those sites share is what Google for Jobs asks of them: a sitemap listing
the vacancy pages, and a schema.org JobPosting on each page (sources/jsonld.py).
This reads exactly that, for any site, configured in boards.toml:

    [[careersite]]
    site = "werkenbij.example.nl"
    sitemap = "https://werkenbij.example.nl/sitemap.xml"
    pattern = "/vacatures/"     # a path holding this is a vacancy page
    place_in_url = true         # worldwide sites only: see below

The order is the government sitemap's (5.5): the sitemap is a complete listing
(so it closes jobs like a board) and a list of URLs whose last segment is the
title; the scope runs on that before a page is fetched; then one request per
page. Everything is a crawl, so the site's robots.txt decides first.

A worldwide corporate site is a different animal: ING lists 703 vacancy pages
(Manila, Bucharest, Amsterdam), EPAM 3,882 (the 5.7 survey, 2026-09-23). The
title scope alone would fetch every software job on the planet. Those sites put
the city in the URL ("/en/job/amsterdam/..."), so `place_in_url` asks the URL to
name a place in the chosen provinces before a page is fetched -- strictly: a URL
that names no Dutch place is a job elsewhere, not one that forgot to say where.

A vacancy is named by its page address ("werkenbij.example.nl/vacatures/..."),
not by the JobPosting's `identifier`: the address is known before the page is
read, which is when "already stored" and "closed" have to be decided.
"""

import re
import xml.etree.ElementTree as ElementTree
from dataclasses import dataclass, field
from urllib.parse import urlparse

import httpx

from joblens.sources.base import Vacancy
from joblens.sources.jsonld import job_postings, posting_to_vacancy
from joblens.sources.polite import CRAWL
from joblens.sources.scope import Scope, Verdict

MAX_CHILD_SITEMAPS = 5  # an index of more than this is a whole site, not its jobs
TRAILING_ID = re.compile(r"[-_]?\d+$")  # "senior-developer-1234" -> "senior-developer"


@dataclass
class SiteStats:
    listed: int = 0  # vacancy pages in the sitemap
    out_of_scope: int = 0
    skipped_known: int = 0
    dropped_invalid: int = 0  # no JobPosting on the page, or the page went
    dropped_no_text: int = 0  # a teaser, an expired posting, an empty text
    left_out: list[str] = field(default_factory=list)
    listed_keys: set[str] = field(default_factory=set)


def page_key(url: str) -> str:
    """A page's stable name: host and path, no scheme, no trailing slash.

    "https://werkenbij.x.nl/vacatures/dev-12/" -> "werkenbij.x.nl/vacatures/dev-12"
    """
    parsed = urlparse(url)
    return f"{parsed.netloc.lower()}{parsed.path.rstrip('/')}"


def title_from(url: str) -> str:
    """The last path segment made of words, in words: good enough for the scope,
    no more. Corporate URLs end in ids -- ING's ".../senior-data-scientist/3121/
    44835281408" -- so the last segment alone would be an empty title."""
    segments = [s for s in urlparse(url).path.split("/") if s]
    worded = [s for s in segments if len(re.sub(r"[-_\d]", "", s)) >= 4]
    slug = worded[-1] if worded else ""
    return TRAILING_ID.sub("", slug).replace("-", " ").replace("_", " ")


class CareerSiteSource:
    name = "careersite"

    def __init__(
        self,
        site: str,
        sitemap: str,
        pattern: str,
        client: httpx.Client,
        *,
        scope: Scope | None = None,
        known_keys: set[str] | frozenset[str] = frozenset(),
        place_in_url: bool = False,
    ):
        self.place_in_url = place_in_url
        self.board = site  # which listing closes its jobs (sources/sightings.py)
        self.sitemap = sitemap
        self.pattern = pattern
        self.client = client
        self.scope = scope
        self.known_keys = known_keys
        self.stats = SiteStats()

    def listing(self) -> list[str]:
        """The vacancy page URLs in the sitemap, following an index one level."""
        urls, children = self._read(self.sitemap)
        wanted = [c for c in children if self._looks_like_jobs(c)] or children
        for child in wanted[:MAX_CHILD_SITEMAPS]:
            urls += self._read(child)[0]
        return list(dict.fromkeys(u for u in urls if self.pattern in urlparse(u).path))

    def _read(self, url: str) -> tuple[list[str], list[str]]:
        """(page URLs, child sitemap URLs) of one sitemap file.

        Redirects are followed, each through the gate and robots.txt: careers.
        ing.com and jobs.ikea.com answer /sitemap.xml with 301 to /en/sitemap.xml.
        Elements are matched on their name, not their namespace: werkenbijipse
        debruggen.nl still writes Google's 2005 namespace (sitemap/0.84), and a
        reader that insisted on sitemaps.org's found an empty sitemap there.
        """
        response = self.client.get(url, extensions=CRAWL, follow_redirects=True)
        response.raise_for_status()
        pages, children = [], []
        for entry in ElementTree.fromstring(response.content):
            kind = _local(entry.tag)
            loc = next((c.text for c in entry if _local(c.tag) == "loc"), None)
            if loc and kind == "url":
                pages.append(loc.strip())
            elif loc and kind == "sitemap":
                children.append(loc.strip())
        return pages, children

    def _looks_like_jobs(self, sitemap: str) -> bool:
        """The path only: on werkenbij.example.nl every URL contains "werken"."""
        words = ("vacature", "vacancy", "vacancies", "job", "career", "werken")
        return any(word in urlparse(sitemap).path.lower() for word in words)

    def fetch(self, limit: int | None = None) -> list[Vacancy]:
        self.stats = SiteStats()
        urls = self.listing()
        self.stats.listed = len(urls)
        self.stats.listed_keys = {f"{self.name}:{page_key(u)}" for u in urls}
        vacancies: list[Vacancy] = []
        for url in urls:
            if limit is not None and len(vacancies) >= limit:
                break
            if self.scope is not None:
                verdict = self.scope.what(title_from(url))
                if verdict.keep and self.place_in_url:
                    words = urlparse(url).path.replace("/", ", ").replace("-", " ")
                    if not self.scope.names_a_chosen_place(words):
                        verdict = Verdict(False, "no chosen place in the URL")
                if not verdict.keep:
                    self.stats.out_of_scope += 1
                    self.stats.left_out.append(f"{title_from(url)} -- {verdict.reason}")
                    continue
            if f"{self.name}:{page_key(url)}" in self.known_keys:
                self.stats.skipped_known += 1
                continue
            vacancy = self.vacancy(url)
            if vacancy is not None:
                vacancies.append(vacancy)
        return vacancies

    def vacancy(self, url: str) -> Vacancy | None:
        response = self.client.get(url, extensions=CRAWL, follow_redirects=True)
        if response.status_code in (404, 410):
            self.stats.dropped_invalid += 1
            return None
        response.raise_for_status()
        postings = job_postings(response.text)
        if not postings:
            self.stats.dropped_invalid += 1
            return None
        vacancy = posting_to_vacancy(postings[0], source=self.name, url=url)
        if vacancy is None:
            self.stats.dropped_no_text += 1
            return None
        # A site in boards.toml is a Dutch employer's, or a worldwide one whose
        # URL just named a Dutch place: a posting that names no country is here.
        # insightfirst.nl's JobPostings name no place at all (2026-09-23), and
        # the Dutch filter would drop them for it.
        return vacancy.model_copy(
            update={
                "source_id": page_key(url),
                "url": url,
                "country": vacancy.country or "NL",
            }
        )


def _local(tag: str) -> str:
    """An XML tag without its namespace: "{http://...}loc" -> "loc"."""
    return tag.rsplit("}", 1)[-1]
