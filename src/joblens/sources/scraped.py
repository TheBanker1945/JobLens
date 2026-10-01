"""Indeed and LinkedIn: two boards that never promised us a feed.

Recruitee, Greenhouse and jobdataapi publish JSON meant for others to read. These
two do not: they are read through the endpoints their own mobile app and their
logged-out pages use, so they can change, or start refusing, without notice. That
difference is what this module is about.

Indeed is read through JobSpy (github.com/speedyapply/JobSpy, MIT), which knows
the endpoint of Indeed's app and follows its changes, so we do not carry that
knowledge ourselves. It is an optional dependency (`uv sync --group scrape`),
imported only when a scrape actually runs. What JobSpy is careless about, we do
here:

- **Runaway loops.** Its Indeed loop has no page guard and can keep going forever
  (its own timeout counts per request), so every scrape runs under a deadline.
- **Personal data.** It collects recruiter e-mails into a column of their own. We
  drop that column, and the unredacted description, before anything is stored.

LinkedIn is read by us, every request of it (since 7.10.5; JobSpy did the search
until then). Its search retries a 429 three times, wears a browser's
User-Agent and hands back an empty list when LinkedIn says no: the three things
the gate in sources/polite.py exists to prevent, and a refusal nobody would
remember. A job without usable text is dropped, never stored: once it is in the
store, an empty vacancy is indistinguishable from a real one and the matcher
will happily rank it. Many description-less jobs in one run means LinkedIn is
throttling us, not that the jobs are empty: the run stops and says so.

Both adapters return `Vacancy`, exactly like the API sources, so nothing
downstream can tell that these two were scraped.
"""

import math
import threading
import time
from collections.abc import Callable, Container
from dataclasses import asdict, dataclass, field
from datetime import date, datetime

import httpx

from joblens.sources.base import Vacancy
from joblens.sources.clean import (
    extract_by_class,
    extract_elements,
    html_to_text,
    redact,
    to_clean_text,
)
from joblens.sources.http import RateLimited, retry_after_seconds
from joblens.sources.scope import Scope

# A row is one job as JobSpy reports it, already out of pandas and into a dict.
Rows = list[dict]
ScrapeFn = Callable[..., Rows]

MIN_TEXT_CHARS = 200  # real vacancies run to thousands; 200 means something broke
KM_PER_MILE = 1.609344  # JobSpy counts in miles; the Netherlands does not
THROTTLE_MIN_SAMPLE = 5  # never call a run throttled on one or two failures


class ScrapeError(Exception):
    """A scrape that did not finish, as opposed to one that found nothing."""


class ScrapeTimeout(ScrapeError):
    def __init__(self, source: str, seconds: float):
        super().__init__(f"{source} did not finish within {seconds:.0f}s")


class LikelyThrottled(ScrapeError):
    """Too many jobs came back without a description: the board is refusing us."""

    def __init__(self, source: str, attempted: int, empty: int):
        super().__init__(
            f"{source}: {empty} of {attempted} jobs came back without a "
            "description, so the run is treated as throttled and stopped"
        )
        self.attempted = attempted
        self.empty = empty


@dataclass
class ScrapeStats:
    """What one fetch did. Printed per run, and stored with the run report."""

    listed: int = 0  # jobs the board's search returned
    skipped_known: int = 0  # already stored, so no description was requested
    dropped_invalid: int = 0  # no id or no url: unusable
    dropped_no_text: int = 0  # description missing, empty or suspiciously short
    kept: int = 0

    def as_dict(self) -> dict[str, int]:
        return asdict(self)


@dataclass
class LinkedInStats(ScrapeStats):
    out_of_scope: int = 0  # never asked for: the card's title or place is outside
    left_out: list[str] = field(default_factory=list)  # why, for the run report
    # every job the search listed, described or not: what "still open" means
    # (sources/sightings.py)
    listed_keys: set[str] = field(default_factory=set)


@dataclass
class LinkedInRun:
    """What every LinkedIn search of one run shares: the descriptions still
    allowed (`max_descriptions_per_run` is per run, not per search), and why
    LinkedIn is not asked again this run, once a search answered with an error
    the gate does not know as a refusal."""

    descriptions_left: int = 40
    stopped: str = ""


class IndeedSource:
    """Indeed's mobile app API, through JobSpy.

    One request brings back up to 100 jobs *including* their descriptions, so
    there is no second round of requests to pace here: the whole scrape is one
    call. The failure mode is different from LinkedIn's — if Indeed changes the
    API, this returns zero jobs rather than an error — which is why the caller
    checks a run that found nothing (see scripts/fetch_vacancies.py).
    """

    name = "indeed"
    site = "indeed.com"  # for the gate in sources/polite.py: one ask per search

    def __init__(
        self,
        search_term: str,
        location: str,
        *,
        country: str = "netherlands",
        distance_km: int = 25,
        hours_old: int = 72,
        min_text_chars: int = MIN_TEXT_CHARS,
        deadline_seconds: float = 180.0,
        scrape: ScrapeFn | None = None,  # tests pass a fake in place of JobSpy
    ):
        self.search_term = search_term
        self.location = location
        self.country = country
        self.distance_km = distance_km
        self.hours_old = hours_old
        self.min_text_chars = min_text_chars
        self.deadline_seconds = deadline_seconds
        self._scrape = scrape or jobspy_scrape
        self.stats = ScrapeStats()

    def fetch(self, limit: int = 50) -> list[Vacancy]:
        self.stats = ScrapeStats()
        rows = run_with_deadline(
            lambda: self._scrape(
                site_name="indeed",
                search_term=self.search_term,
                location=self.location,
                country_indeed=self.country,
                distance=km_to_miles(self.distance_km),
                hours_old=self.hours_old,
                results_wanted=limit,
                description_format="html",  # so our own clean.py handles the text
                verbose=0,
            ),
            self.deadline_seconds,
            self.name,
        )
        self.stats.listed = len(rows)

        vacancies = []
        for row in rows:
            text = to_clean_text(row.get("description") or "")
            if len(text) < self.min_text_chars:
                self.stats.dropped_no_text += 1
                continue
            vacancy = build_vacancy(self.name, row, text, id_prefix="in-")
            if vacancy is None:
                self.stats.dropped_invalid += 1
                continue
            vacancies.append(vacancy)
        self.stats.kept = len(vacancies)
        return vacancies


class LinkedInSource:
    """LinkedIn's logged-out (guest) pages, read by us, through the gate.

    Two kinds of request, both the ones a guest visitor's browser makes: a
    search page of ten job cards, and a job's description fragment. Both go
    through `client`, so the gate paces them, counts them and remembers a 429
    or a 403, and both carry our own User-Agent (answered with 200, 2026-10-01).

    The volume stays small: a card's title and place are checked against the
    scope before its description is asked for, a job already stored or passed
    over costs no request, `run` caps the descriptions of the whole run, and
    `delay_seconds` sits between requests on top of the gate's own pause.

    robots.txt disallows all of this (/jobs-guest/, and "/" for any crawler
    LinkedIn did not approve). Reading it anyway is Mahdi's decision of
    2026-10-01, so these requests are not marked CRAWL. Every other rule of the
    gate holds: a refusal is remembered, and nothing gets around one.
    """

    name = "linkedin"
    site = "linkedin.com"
    gate_sees_requests = True  # through `client`: fetch_vacancies.py need not ask
    search_url = (
        "https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search"
    )
    description_url = "https://www.linkedin.com/jobs-guest/jobs/api/jobPosting/{id}"
    description_class = "show-more-less-html__markup"
    job_url = "https://www.linkedin.com/jobs/view/{id}"
    page_size = 10  # cards per search page, measured 2026-10-01

    def __init__(
        self,
        search_term: str,
        location: str,
        client: httpx.Client,
        *,
        distance_km: int = 25,
        hours_old: int = 72,
        scope: Scope | None = None,
        known_keys: Container[str] = (),
        run: LinkedInRun | None = None,
        delay_seconds: float = 2.5,
        max_empty_share: float = 0.3,  # above this, assume throttling, not bad luck
        min_text_chars: int = MIN_TEXT_CHARS,
    ):
        self.search_term = search_term
        self.location = location
        self.client = client
        self.distance_km = distance_km
        self.hours_old = hours_old
        self.scope = scope
        self.known_keys = known_keys
        self.run = run or LinkedInRun()
        self.delay_seconds = delay_seconds
        self.max_empty_share = max_empty_share
        self.min_text_chars = min_text_chars
        self.stats = LinkedInStats()
        self._asked = False  # a pause before every request but the first

    def fetch(self, limit: int = 25) -> list[Vacancy]:
        self.stats = LinkedInStats()
        if self.run.stopped:
            raise ScrapeError(f"{self.name}: not asked again: {self.run.stopped}")
        rows = self.listing(limit)
        self.stats.listed = len(rows)
        self.stats.listed_keys = {
            f"{self.name}:{source_id(row, prefix='li-')}" for row in rows
        }

        vacancies = []
        attempted = 0
        for row in rows:
            job_id = source_id(row, prefix="li-")
            if self.scope is not None:
                place = row["location"] or ""  # an unknown place is kept
                verdict = self.scope.check_listing(row["title"], place)
                if not verdict.keep:
                    self.stats.out_of_scope += 1
                    self.stats.left_out.append(f"{row['title']} -- {verdict.reason}")
                    continue
            if f"{self.name}:{job_id}" in self.known_keys:
                self.stats.skipped_known += 1
                continue
            if self.run.descriptions_left <= 0:
                break
            self.run.descriptions_left -= 1
            attempted += 1
            text = self.description(job_id)

            if text is None or len(text) < self.min_text_chars:
                self.stats.dropped_no_text += 1
                self.stop_if_throttled(attempted)
                continue
            vacancy = build_vacancy(self.name, row, text, id_prefix="li-")
            if vacancy is None:
                self.stats.dropped_invalid += 1
                continue
            vacancies.append(vacancy)

        self.stats.kept = len(vacancies)
        return vacancies

    def listing(self, limit: int) -> Rows:
        """The search's jobs, at most `limit`, a page of ten cards per request.
        A page with fewer than ten, or none that are new, is the last."""
        found: dict[str, dict] = {}
        start = 0
        for _ in range(math.ceil(limit / self.page_size)):
            response = self.get(
                self.search_url,
                params={
                    "keywords": self.search_term,
                    "location": self.location,
                    "distance": km_to_miles(self.distance_km),
                    "f_TPR": f"r{self.hours_old * 3600}",  # posted in the last ...
                    "start": start,
                },
            )
            if response.status_code >= 400:
                # A 429 or a 403 never gets here: the gate refused it and
                # remembers. Anything else is not a known refusal (LinkedIn is
                # said to answer 999; not seen yet), so it is not remembered,
                # but LinkedIn is not asked again tonight.
                self.run.stopped = f"a search answered HTTP {response.status_code}"
                raise ScrapeError(f"{self.name}: {self.run.stopped}")
            cards = job_cards(response.text)
            new = [card for card in cards if source_id(card, prefix="li-") not in found]
            for card in new:
                found[source_id(card, prefix="li-")] = card
            if len(cards) < self.page_size or not new:
                break
            start += len(cards)
        return list(found.values())[:limit]

    def description(self, job_id: str) -> str | None:
        """The vacancy text for one job, or None if LinkedIn did not give us one.

        A timeout or a connection error is a missing description like any other:
        it is counted, and if it keeps happening `stop_if_throttled` ends the run.
        Silence is the one thing we do not allow.
        """
        try:
            response = self.get(self.description_url.format(id=job_id))
        except httpx.HTTPError:
            return None
        if response.status_code == 429:  # a client without the gate (tests)
            raise RateLimited(self.name, retry_after_seconds(response.headers))
        if response.status_code >= 400:
            return None
        # A guest page for a job that wants a login has no description div at all.
        inner = extract_by_class(response.text, self.description_class)
        return to_clean_text(inner) if inner else None

    def get(self, url: str, **options) -> httpx.Response:
        if self._asked:
            time.sleep(self.delay_seconds)
        self._asked = True
        return self.client.get(url, **options)

    def stop_if_throttled(self, attempted: int) -> None:
        if attempted < THROTTLE_MIN_SAMPLE:
            return
        if self.stats.dropped_no_text / attempted > self.max_empty_share:
            raise LikelyThrottled(self.name, attempted, self.stats.dropped_no_text)


URN = "urn:li:jobPosting:"  # a card's data-entity-urn: the job's id


def job_cards(page: str) -> Rows:
    """The job cards of one search page, as rows in the shape JobSpy gave them
    until 7.10.5 (so stored LinkedIn vacancies keep one shape).

    A card is the element whose data-entity-urn names a job posting; title,
    company and place are the texts of three classes inside it, and the date
    is the `datetime` of its <time>. A card without an id or a title is left
    out: nothing could be stored from it.
    """
    ids: list[str] = []

    def is_card(attrs: dict[str, str | None]) -> bool:
        urn = attrs.get("data-entity-urn") or ""
        if urn.startswith(URN):
            ids.append(urn.removeprefix(URN))  # one per element found, in order
            return True
        return False

    cards = extract_elements(page, is_card)
    rows = []
    for job_id, inner in zip(ids, cards, strict=True):
        title = text_of(inner, "base-search-card__title")
        if not job_id.isdigit() or not title:
            continue
        rows.append(
            {
                "id": f"li-{job_id}",
                "site": "linkedin",
                "job_url": LinkedInSource.job_url.format(id=job_id),
                "title": title,
                "company": text_of(inner, "base-search-card__subtitle") or None,
                "location": text_of(inner, "job-search-card__location") or None,
                "date_posted": first_attribute(inner, "datetime"),
            }
        )
    return rows


def text_of(raw: str, class_name: str) -> str:
    inner = extract_by_class(raw, class_name)
    return html_to_text(inner) if inner else ""


def first_attribute(raw: str, name: str) -> str | None:
    """The value of the first `name` attribute in `raw` (a <time>'s datetime)."""
    values: list[str] = []

    def note(attrs: dict[str, str | None]) -> bool:
        if attrs.get(name) and not values:
            values.append(attrs[name] or "")
        return False  # nothing is extracted; every start tag is looked at

    extract_elements(raw, note)
    return values[0] if values else None


def jobspy_scrape(**options) -> Rows:
    """The real scraper: JobSpy in, plain dicts out.

    Imported here and nowhere else, so the package works without the `scrape`
    dependency group, and so pandas never leaks past this function.
    """
    from jobspy import scrape_jobs

    frame = scrape_jobs(**options)
    if frame is None or frame.empty:
        return []
    # NaN is pandas' "no value" and has no equivalent in JSON; `object` keeps the
    # strings as they are instead of turning the column into floats.
    filled = frame.astype(object).where(frame.notna(), None)
    return filled.to_dict(orient="records")


def run_with_deadline(work: Callable[[], Rows], seconds: float, source: str) -> Rows:
    """Run `work` in a daemon thread and give up on it after `seconds`.

    A thread cannot be killed from outside, but a daemon thread does not keep the
    process alive, so an abandoned scrape cannot wedge the nightly run. That
    matters here: JobSpy's Indeed loop keeps requesting pages until it has enough
    *new* jobs, and a page of repeats leaves it looping.
    """
    outcome: list[tuple[str, object]] = []

    def attempt() -> None:
        try:
            outcome.append(("rows", work()))
        except Exception as error:  # re-raised below, on the calling thread
            outcome.append(("error", error))

    thread = threading.Thread(target=attempt, name=f"scrape-{source}", daemon=True)
    thread.start()
    thread.join(seconds)

    if not outcome:
        raise ScrapeTimeout(source, seconds)
    kind, value = outcome[0]
    if kind == "error":
        raise value  # type: ignore[misc]
    return value  # type: ignore[return-value]


def km_to_miles(km: float) -> int:
    """JobSpy's `distance` is in miles and defaults to 50: a radius that covers
    most of the Netherlands. Config says kilometres; this is where it converts."""
    return max(1, round(km / KM_PER_MILE))


def build_vacancy(
    source: str, row: dict, text: str, *, id_prefix: str
) -> Vacancy | None:
    """One JobSpy row as a `Vacancy`, or None if it lacks an id or a url."""
    job_id = source_id(row, prefix=id_prefix)
    url = row.get("job_url")
    if not job_id or not url:
        return None
    city, country = split_location(row.get("location"))
    return Vacancy(
        source=source,
        source_id=job_id,
        url=str(url),
        title=(row.get("title") or "").strip(),
        company=row.get("company"),
        city=city,
        country=country,
        posted_at=parse_date(row.get("date_posted")),
        text=text,
        raw=storable(row),
    )


def source_id(row: dict, *, prefix: str) -> str:
    """JobSpy prefixes its ids per site ("in-3fa1", "li-4456868563"). The prefix
    is its way of keeping sites apart; `Vacancy.source` already does that here."""
    return str(row.get("id") or "").removeprefix(prefix).strip()


def split_location(display: object) -> tuple[str | None, str | None]:
    """JobSpy formats a location as "Amsterdam, North Holland, Netherlands", and
    leaves parts out when it does not know them (LinkedIn gives no country)."""
    parts = [part.strip() for part in str(display or "").split(",") if part.strip()]
    if not parts:
        return None, None
    country = "NL" if parts[-1].lower() in ("netherlands", "nederland", "nl") else None
    city = parts[0] if parts[0] != parts[-1] or country is None else None
    return city, country


def parse_date(value: object) -> datetime | None:
    """`date_posted` arrives as a date, a timestamp or a "2026-09-18" string."""
    if isinstance(value, datetime):
        return value
    if isinstance(value, date):
        return datetime(value.year, value.month, value.day)
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value))
    except ValueError:
        return None


def storable(row: dict) -> dict:
    """The row as we keep it: no contact details, and JSON-safe.

    `description` goes because `Vacancy.text` already holds the same text with
    e-mails and phone numbers removed; keeping the original would put them back
    on disk. `emails` is a column JobSpy fills by harvesting the description.
    """
    dropped = ("description", "emails")
    kept = {
        key: value.isoformat() if isinstance(value, date | datetime) else value
        for key, value in row.items()
        if key not in dropped and value is not None
    }
    return redact(kept)  # company fields can carry a contact address too
