"""Indeed and LinkedIn: two boards that never promised us a feed.

Recruitee, Greenhouse and jobdataapi publish JSON meant for others to read. These
two do not: they are read through the endpoints their own mobile app and their
logged-out pages use, so they can change, or start refusing, without notice. That
difference is what this module is about.

JobSpy (github.com/speedyapply/JobSpy, MIT) knows those endpoints and follows
their changes, so we do not carry that knowledge ourselves. It is an optional
dependency (`uv sync --group scrape`), imported only when a scrape actually runs.

What JobSpy is careless about, we do here:

- **Descriptions.** It fires one request per description with no pause and
  swallows every error, so a throttled LinkedIn hands back jobs with an empty
  description and no complaint. We ask it for the *listing only*, then fetch
  descriptions ourselves: paced, with our own User-Agent, and only for jobs that
  are not in the store yet. A job without usable text is dropped, never stored:
  once it is in the store, an empty vacancy is indistinguishable from a real one
  and the matcher will happily rank it.
- **Throttling.** Many description-less jobs in one run means LinkedIn is
  throttling us, not that the jobs are empty. The run stops and says so.
- **Runaway loops.** Its Indeed loop has no page guard and can keep going forever
  (its own timeout counts per request), so every scrape runs under a deadline.
- **Personal data.** It collects recruiter e-mails into a column of their own. We
  drop that column, and the unredacted description, before anything is stored.

Both adapters return `Vacancy`, exactly like the API sources, so nothing
downstream can tell that these two were scraped.
"""

import threading
import time
from collections.abc import Callable, Container
from dataclasses import asdict, dataclass
from datetime import date, datetime

import httpx

from joblens.sources.base import Vacancy
from joblens.sources.clean import extract_by_class, redact, to_clean_text
from joblens.sources.http import RateLimited

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


class IndeedSource:
    """Indeed's mobile app API, through JobSpy.

    One request brings back up to 100 jobs *including* their descriptions, so
    there is no second round of requests to pace here: the whole scrape is one
    call. The failure mode is different from LinkedIn's — if Indeed changes the
    API, this returns zero jobs rather than an error — which is why the caller
    checks a run that found nothing (see scripts/fetch_vacancies.py).
    """

    name = "indeed"

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
    """LinkedIn's logged-out (guest) endpoints, in two halves.

    JobSpy does the search listing, which it paces 3-7 seconds per page. We do the
    descriptions ourselves, from the same fragment endpoint a guest visitor's
    browser calls, because that is the request that gets an IP throttled and the
    one JobSpy handles worst.

    Three rules make the volume small: jobs already in the store cost no request
    at all, `max_descriptions` caps a run, and `delay_seconds` sits between the
    requests that remain.
    """

    name = "linkedin"
    description_url = "https://www.linkedin.com/jobs-guest/jobs/api/jobPosting/{id}"
    description_class = "show-more-less-html__markup"

    def __init__(
        self,
        search_term: str,
        location: str,
        client: httpx.Client,
        *,
        distance_km: int = 25,
        hours_old: int = 72,
        known_keys: Container[str] = (),
        max_descriptions: int = 40,
        delay_seconds: float = 2.5,
        max_empty_share: float = 0.3,  # above this, assume throttling, not bad luck
        min_text_chars: int = MIN_TEXT_CHARS,
        deadline_seconds: float = 300.0,
        scrape: ScrapeFn | None = None,
    ):
        self.search_term = search_term
        self.location = location
        self.client = client
        self.distance_km = distance_km
        self.hours_old = hours_old
        self.known_keys = known_keys
        self.max_descriptions = max_descriptions
        self.delay_seconds = delay_seconds
        self.max_empty_share = max_empty_share
        self.min_text_chars = min_text_chars
        self.deadline_seconds = deadline_seconds
        self._scrape = scrape or jobspy_scrape
        self.stats = ScrapeStats()

    def fetch(self, limit: int = 25) -> list[Vacancy]:
        self.stats = ScrapeStats()
        rows = run_with_deadline(
            lambda: self._scrape(
                site_name="linkedin",
                search_term=self.search_term,
                location=self.location,
                distance=km_to_miles(self.distance_km),
                hours_old=self.hours_old,
                results_wanted=limit,
                linkedin_fetch_description=False,  # the listing only; we do the rest
                verbose=0,
            ),
            self.deadline_seconds,
            self.name,
        )
        self.stats.listed = len(rows)

        vacancies = []
        attempted = 0
        for row in rows:
            if attempted >= self.max_descriptions:
                break
            job_id = source_id(row, prefix="li-")
            if not job_id:
                self.stats.dropped_invalid += 1
                continue
            if f"{self.name}:{job_id}" in self.known_keys:
                self.stats.skipped_known += 1
                continue

            if attempted:  # a pause between requests, never before the first
                time.sleep(self.delay_seconds)
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

    def description(self, job_id: str) -> str | None:
        """The vacancy text for one job, or None if LinkedIn did not give us one.

        A timeout or a connection error is a missing description like any other:
        it is counted, and if it keeps happening `stop_if_throttled` ends the run.
        Silence is the one thing we do not allow.
        """
        try:
            response = self.client.get(self.description_url.format(id=job_id))
        except httpx.HTTPError:
            return None
        if response.status_code == 429:
            retry_after = response.headers.get("retry-after")
            raise RateLimited(self.name, float(retry_after) if retry_after else None)
        if response.status_code >= 400:
            return None
        # A guest page for a job that wants a login has no description div at all.
        inner = extract_by_class(response.text, self.description_class)
        return to_clean_text(inner) if inner else None

    def stop_if_throttled(self, attempted: int) -> None:
        if attempted < THROTTLE_MIN_SAMPLE:
            return
        if self.stats.dropped_no_text / attempted > self.max_empty_share:
            raise LikelyThrottled(self.name, attempted, self.stats.dropped_no_text)


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
