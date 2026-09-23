"""Every request a source makes passes one gate, so politeness is a place, not a habit.

Until 5.1 politeness was a `time.sleep(1.5)` at the bottom of the fetch loop:
after every search, whoever it went to. That paused between Adyen's Greenhouse
board and Channable's Recruitee board, which share nothing, and did nothing
about the thing that actually gets an address blocked -- many requests to one
*site*. It also forgot everything at exit, so a board that throttled us last
night got the full run again tonight.

The gate does four things, all per site:

- **Paces.** Between two requests to the same site: `delay_seconds`, plus up to
  `jitter_seconds` so requests do not tick like a clock. Different sites do not
  wait for each other.
- **Counts, and stops at a budget.** `max_requests_per_site` per run. A loop that
  runs away (JobSpy's Indeed pager could, see 2.4) ends at our number, not at the
  site's patience.
- **Recognises a refusal,** including the ones that arrive as a normal page: a
  429, a 403, a Cloudflare challenge, a consent wall where the vacancy should be.
- **Remembers it.** A refusal is written to data/raw/fetch-state.json at once,
  with `blocked_until`. Later runs do not ask that site again until then, and
  each refusal in a row doubles the wait.

A *site* is the platform, not the host name: channable.recruitee.com and
nmbrs.recruitee.com are two boards on one platform's servers, so they share one
pace and one budget. See `site_of`.

What this is not: a way around a refusal. No proxies, no rotating addresses, no
browser disguise, no retries. When a site says no, the source stops and the run
report says so; a person decides what happens next.

robots.txt governs what is *crawled*, not every request. Measured 2026-09-22:
jobdataapi.com disallows /api/ and api.smartrecruiters.com disallows
everything, while both document those very endpoints as public APIs. robots.txt
is written for crawlers and search indexes; for a documented API the provider's
docs and rate limits are the permission. A source that reads web pages -- a
sitemap and the pages it lists, since 5.5 -- marks its requests with `CRAWL`,
and only those are checked against the site's robots.txt (read with protego,
which follows RFC 9309 including the `*` and `$` wildcards that
nationalevacaturebank.nl and jobbird.com use). A Crawl-delay or Request-rate
there can slow the gate down for that site, never speed it up.
"""

import json
import random
import time
from collections import Counter
from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
from protego import Protego

from joblens.sources.http import RateLimited, retry_after_seconds

# Pass as `extensions=CRAWL` on a request that reads a web page rather than a
# documented API: the transport then checks robots.txt first.
CRAWL = {"joblens_crawl": True}
ROBOTS_AGENT = "JobLens"  # the product token of our User-Agent
MAX_ROBOTS_REDIRECTS = 5  # RFC 9309: follow at least five


class Refused(Exception):
    """A request that was not answered: the site said no, or we did not ask.

    `status` is what the run report records. Unlike a failure, a refusal by the
    site is an answer: it is remembered, and the next run respects it.
    """

    status = "blocked"

    def __init__(self, site: str, detail: str):
        super().__init__(f"{site}: {detail}")
        self.site = site
        self.detail = detail


class CoolingDown(Refused):
    """The site refused us in this run or an earlier one; we wait before asking."""

    status = "cooling_down"


class OverBudget(Refused):
    """We asked this site as often as one run is allowed to."""

    status = "over_budget"


class Disallowed(Refused):
    """The site's robots.txt does not allow this page to be crawled.

    Not remembered as a refusal: it is a standing rule rather than a mood, and
    it is read again every run. A source configured to crawl what robots.txt
    forbids is a mistake in sources.toml, and the report says so."""

    status = "disallowed"


# Pages that answer "no" with an ordinary 200 or 503. Each marker was seen on a
# real refusal before it was added here, never guessed. Generic words ("captcha",
# "challenge-platform") are not markers: ordinary pages carry them too, and
# Cloudflare loads a challenge-platform script on normal pages of sites it guards.
REFUSAL_PAGES: tuple[tuple[str, str], ...] = (
    # werkzoeken.nl and ictergezocht.nl, 2026-09-22, even for robots.txt.
    ("_cf_chl_opt", "a Cloudflare challenge page"),
    # nationalevacaturebank.nl, 2026-09-22, at the end of the redirect below.
    (
        "<title>dpg media privacy gate</title>",
        "a consent wall (DPG Media Privacy Gate)",
    ),
)
SCAN_CHARS = 65_536  # a refusal page says so near the top

# Redirects that are a refusal: the site sends us to a page that is not the one
# we asked for, and never will be. Checked on the redirect itself, so the refusal
# is held against the site we asked, not against the host of the wall.
# nationalevacaturebank.nl, 2026-09-22: every vacancy answers 302 to here.
REFUSAL_REDIRECTS: tuple[tuple[str, str], ...] = (
    ("myprivacy.dpgmedia.nl", "a consent wall (DPG Media Privacy Gate)"),
)


def site_of(host: str) -> str:
    """The platform behind a host name: "channable.recruitee.com" -> "recruitee.com".

    The last two labels of the name. Right for every site JobLens asks today
    (.nl, .com, .io); a .co.uk would need the public-suffix list, and nothing
    here is one. A custom domain on a shared platform (vacatures.coloriet.nl is
    Recruitee underneath) counts as its own site: from outside there is no way to
    tell, and pacing it separately is still polite.
    """
    labels = host.lower().rstrip(".").split(".")
    return ".".join(labels[-2:])


@dataclass(frozen=True)
class Rules:
    """The numbers from the [politeness] table in sources.toml."""

    delay_seconds: float = 1.5
    jitter_seconds: float = 1.0
    max_requests_per_site: int = 200
    first_cooldown_hours: float = 12.0
    max_cooldown_days: float = 7.0
    # [politeness.sites."jobdataapi.com"]: a site that needs other numbers
    sites: Mapping[str, Mapping] = field(default_factory=dict)

    @classmethod
    def from_config(cls, table: Mapping) -> "Rules":
        fields = (
            "delay_seconds",
            "jitter_seconds",
            "max_requests_per_site",
            "first_cooldown_hours",
            "max_cooldown_days",
        )
        return cls(
            **{name: table[name] for name in fields if name in table},
            sites=table.get("sites", {}),
        )

    def delay(self, site: str) -> float:
        return self.sites.get(site, {}).get("delay_seconds", self.delay_seconds)

    def budget(self, site: str) -> int:
        return self.sites.get(site, {}).get("max_requests", self.max_requests_per_site)

    def cooldown(self, strikes: int) -> timedelta:
        """12 hours after one refusal, then 24, 48, ... up to a week.

        Twelve hours so that a nightly run which is refused once is simply tried
        again the next night; only a site that keeps refusing is left alone for
        longer. The doubling is what stops us knocking on a door every night.
        """
        hours = self.first_cooldown_hours * 2 ** (strikes - 1)
        return min(timedelta(hours=hours), timedelta(days=self.max_cooldown_days))


@dataclass
class SiteState:
    """What we remember about one site that refused us."""

    strikes: int  # refusals in a row; a clean run sets this back to zero
    blocked_until: datetime
    reason: str
    refused_at: datetime


class FetchState:
    """The sites that refused us, and until when: data/raw/fetch-state.json.

    Only refusals are kept. A site that answers normally has no entry, so the
    file reads as a list of who is currently saying no, and why. To ask a site
    before its time is up, delete its entry: a person's decision, not the code's.
    """

    def __init__(self, path: Path | None = None):
        self.path = path  # None: kept in memory only (tests)
        self.sites: dict[str, SiteState] = {}

    @classmethod
    def load(cls, path: Path) -> "FetchState":
        state = cls(path)
        if path.exists():
            raw = json.loads(path.read_text(encoding="utf-8"))
            for site, entry in raw.get("sites", {}).items():
                state.sites[site] = SiteState(
                    strikes=entry["strikes"],
                    blocked_until=datetime.fromisoformat(entry["blocked_until"]),
                    reason=entry["reason"],
                    refused_at=datetime.fromisoformat(entry["refused_at"]),
                )
        return state

    def save(self) -> None:
        if self.path is None:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        sites = {
            site: {
                **asdict(entry),
                "blocked_until": entry.blocked_until.isoformat(timespec="seconds"),
                "refused_at": entry.refused_at.isoformat(timespec="seconds"),
            }
            for site, entry in sorted(self.sites.items())
        }
        text = json.dumps({"sites": sites}, indent=2) + "\n"
        self.path.write_text(text, encoding="utf-8")


class Gate:
    """Decides, per request, whether and when a site may be asked.

    `ask` before a request, then `answered`, `refused` or `failed` after it;
    `finish` at the end of a run. `PoliteTransport` does this for every httpx
    request. The scraped sources call it themselves, because JobSpy sends its
    own requests.
    """

    def __init__(
        self,
        state: FetchState,
        rules: Rules | None = None,
        *,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,  # for pacing
        now: Callable[[], datetime] = lambda: datetime.now(UTC),  # for cooldowns
        jitter: Callable[[], float] = random.random,  # 0..1, a share of jitter_seconds
    ):
        self.state = state
        self.rules = rules or Rules()
        self._sleep, self._clock, self._now, self._jitter = sleep, clock, now, jitter
        self.requests: Counter[str] = Counter()  # per site, this run
        self._last: dict[str, float] = {}  # when each site last answered
        self._answered: set[str] = set()
        self._refused: set[str] = set()
        self._robots_delay: dict[str, float] = {}  # a site's own Crawl-delay

    def slow_down(self, site: str, seconds: float) -> None:
        """A site asks for more time between requests (robots.txt Crawl-delay or
        Request-rate). Only ever slower than sources.toml, never faster."""
        self._robots_delay[site] = max(self._robots_delay.get(site, 0.0), seconds)

    def ask(self, site: str) -> None:
        """Wait for our turn at `site`, or raise if we should not ask it at all."""
        entry = self.state.sites.get(site)
        if entry and entry.blocked_until > self._now():
            raise CoolingDown(
                site,
                f"refused us ({entry.reason}); not asking again until "
                f"{entry.blocked_until:%Y-%m-%d %H:%M} UTC",
            )
        budget = self.rules.budget(site)
        if self.requests[site] >= budget:
            raise OverBudget(site, f"{budget} requests this run, the most allowed")
        if site in self._last:
            delay = max(self.rules.delay(site), self._robots_delay.get(site, 0.0))
            pause = delay + self.rules.jitter_seconds * self._jitter()
            wait = self._last[site] + pause - self._clock()
            if wait > 0:
                self._sleep(wait)
        self.requests[site] += 1

    def answered(self, site: str) -> None:
        """The site answered normally. The next request waits from now."""
        self._last[site] = self._clock()
        self._answered.add(site)

    def failed(self, site: str) -> None:
        """No answer at all (a timeout, a dropped connection). Not a refusal, so
        nothing is remembered, but the next request still waits from now."""
        self._last[site] = self._clock()

    def refused(
        self, site: str, reason: str, retry_after: float | None = None
    ) -> datetime:
        """Remember a refusal, on disk at once, and return when we may ask again.

        The wait is the longer of what the site asked for (`Retry-After`) and our
        own schedule. Written immediately rather than at the end of the run, so a
        run that crashes right after a refusal still leaves the memory behind.
        """
        now = self._now()
        self._last[site] = self._clock()
        self._refused.add(site)
        previous = self.state.sites.get(site)
        strikes = (previous.strikes if previous else 0) + 1
        wait = max(self.rules.cooldown(strikes), timedelta(seconds=retry_after or 0))
        self.state.sites[site] = SiteState(strikes, now + wait, reason, now)
        self.state.save()
        return now + wait

    def finish(self) -> None:
        """End of run: a site that answered and never refused is forgiven."""
        forgiven = (self._answered - self._refused) & set(self.state.sites)
        for site in forgiven:
            del self.state.sites[site]
        if forgiven:
            self.state.save()


class PoliteTransport(httpx.BaseTransport):
    """An httpx transport that sends every request through a `Gate`.

    A transport sits underneath the client, so the adapters (Recruitee,
    Greenhouse, jobdataapi, LinkedIn's descriptions) call `client.get` exactly as
    before and cannot forget to be polite: there is no other way out.
    """

    def __init__(self, gate: Gate, inner: httpx.BaseTransport | None = None):
        self.gate = gate
        self.inner = inner or httpx.HTTPTransport()
        self._robots: dict[str, Protego | None] = {}  # per origin, read once a run

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        site = site_of(request.url.host)
        if request.extensions.get("joblens_crawl"):
            self._obey_robots(request, site)
        self.gate.ask(site)
        try:
            response = self.inner.handle_request(request)
            response.read()  # small JSON and HTML; needed to spot a refusal page
        except httpx.HTTPError:
            self.gate.failed(site)
            raise

        if response.status_code == 429:
            retry_after = retry_after_seconds(response.headers)
            self.gate.refused(site, "HTTP 429", retry_after)
            raise RateLimited(site, retry_after)
        reason = refusal_in(response)
        if reason:
            until = self.gate.refused(site, reason)
            raise Refused(
                site, f"{reason}; not asking again until {until:%Y-%m-%d %H:%M} UTC"
            )
        self.gate.answered(site)
        return response

    def close(self) -> None:
        self.inner.close()

    def _obey_robots(self, request: httpx.Request, site: str) -> None:
        origin = f"{request.url.scheme}://{request.url.netloc.decode()}"
        if origin not in self._robots:
            self._robots[origin] = self._read_robots(request, origin)
        rules = self._robots[origin]
        if rules is None:
            raise Disallowed(site, "robots.txt could not be read, so nothing is")
        if not rules.can_fetch(str(request.url), ROBOTS_AGENT):
            raise Disallowed(site, f"robots.txt disallows {request.url.path}")
        if delay := rules.crawl_delay(ROBOTS_AGENT):
            self.gate.slow_down(site, float(delay))
        if rate := rules.request_rate(ROBOTS_AGENT):
            self.gate.slow_down(site, rate.seconds / rate.requests)

    def _read_robots(self, request: httpx.Request, origin: str) -> Protego | None:
        """The site's robots.txt, through the gate like any request, so a
        challenge page on robots.txt itself (werkzoeken.nl) is still a refusal.

        RFC 9309: a missing robots.txt (4xx) allows everything; one that cannot
        be read (5xx, no answer) allows nothing, until the next run. Redirects
        are followed, up to five, even to another host: werkenbijantonius.nl
        answers 301 to www.werkenbijantonius.nl/robots.txt. The first version
        read a redirect as "cannot be read" and so shut out every site that
        moves between www and its bare name -- 4 of 82 in the 5.7 survey.
        """
        extensions = {
            k: v for k, v in request.extensions.items() if k != "joblens_crawl"
        }
        url = f"{origin}/robots.txt"
        for _ in range(MAX_ROBOTS_REDIRECTS + 1):
            robots = httpx.Request(
                "GET",
                url,
                headers={"User-Agent": request.headers.get("user-agent", "")},
                extensions=extensions,
            )
            try:
                response = self.handle_request(robots)
            except httpx.HTTPError:
                return None
            if not response.is_redirect:
                break
            url = str(robots.url.join(response.headers.get("location", "")))
        else:
            return None  # redirected more than five times: cannot be read
        if 400 <= response.status_code < 500:
            return Protego.parse("")
        if response.status_code >= 300:
            return None
        return Protego.parse(response.text)


def refusal_in(response: httpx.Response) -> str | None:
    """Why this response is a refusal, or None if it is an answer.

    403 is always a refusal. A 200 or 503 is one when the page is a known
    refusal page (REFUSAL_PAGES), a redirect when it leads to a known wall
    (REFUSAL_REDIRECTS). Everything else passes: a 404 is a board that does not
    exist, a 500 a server that broke, and the caller reports those.
    """
    if response.is_redirect:
        target = httpx.URL(response.headers.get("location", "")).host
        return next((name for host, name in REFUSAL_REDIRECTS if target == host), None)
    content_type = response.headers.get("content-type", "")
    page = None
    if "html" in content_type:
        head = response.content[:SCAN_CHARS].decode("utf-8", errors="replace")
        page = next(
            (name for marker, name in REFUSAL_PAGES if marker in head.lower()), None
        )
    if response.status_code == 403:
        return f"HTTP 403, {page}" if page else "HTTP 403"
    if response.status_code in (200, 503) and page:
        return page
    return None
