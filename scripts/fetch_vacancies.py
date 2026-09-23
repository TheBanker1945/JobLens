"""Fetch real vacancies from the public sources in sources.toml into data/raw/.

data/raw/ is never committed: vacancy texts are someone else's copyright, and we
only need them locally to test extraction and search.

Sources come in two kinds. The API sources (Recruitee, Greenhouse,
SmartRecruiters, jobdataapi) publish JSON meant to be read; the employer boards
among them are listed in boards.toml. The scraped ones (Indeed, LinkedIn) do not, need
the optional dependency group, and can fail in quieter ways -- see
src/joblens/sources/scraped.py.

Usage:
    uv run python scripts/fetch_vacancies.py                    # all sources
    uv run python scripts/fetch_vacancies.py --source recruitee --limit 20
    uv run python scripts/fetch_vacancies.py --all-countries    # skip the NL filter
    uv run python scripts/fetch_vacancies.py --no-scope         # store all Dutch jobs

    uv sync --group scrape                                      # once, for these:
    uv run python scripts/fetch_vacancies.py --source indeed

Every run writes a report to data/raw/runs/ and exits non-zero when something
looks wrong (a source that found nothing at all, descriptions that never
arrived, a board refusing us), so a scheduled run can tell us it went bad.

Every request goes through one gate (src/joblens/sources/polite.py): paced per
site, capped per run, and a site that refuses us is remembered in
data/raw/fetch-state.json, so the next run leaves it alone for a while.

Every listing also says what is still open. A job an employer board no longer
lists has closed, and data/raw/sightings.json says since when
(src/joblens/sources/sightings.py); matching leaves closed jobs out.
"""

import argparse
import sys
from datetime import UTC, datetime
from pathlib import Path

import httpx

from joblens.sources.boards import load_config
from joblens.sources.careersite import CareerSiteSource
from joblens.sources.eures import EuresSource, nuts_codes
from joblens.sources.greenhouse import GreenhouseSource
from joblens.sources.http import RateLimited, new_client
from joblens.sources.jobdataapi import JobDataApiSource
from joblens.sources.netherlands import is_dutch, select
from joblens.sources.overheid import SITEMAP, OverheidSource
from joblens.sources.polite import FetchState, Gate, PoliteTransport, Refused, Rules
from joblens.sources.recruitee import RecruiteeSource
from joblens.sources.report import RunReport, SearchRun
from joblens.sources.scope import Scope
from joblens.sources.scraped import (
    IndeedSource,
    LikelyThrottled,
    LinkedInSource,
    ScrapeTimeout,
)
from joblens.sources.sightings import BOARD_SOURCES, Sightings
from joblens.sources.smartrecruiters import SmartRecruitersSource
from joblens.sources.store import VacancyStore
from joblens.sources.workday import WorkdaySource

ROOT = Path(__file__).parent.parent
RAW_DIR = ROOT / "data" / "raw" / "vacancies"
RUNS_DIR = ROOT / "data" / "raw" / "runs"
STATE_PATH = ROOT / "data" / "raw" / "fetch-state.json"
SIGHTINGS_PATH = ROOT / "data" / "raw" / "sightings.json"
SOURCES = (
    "recruitee",
    "greenhouse",
    "smartrecruiters",
    "workday",
    "careersite",
    "overheid",
    "jobdataapi",
    "eures",
    "indeed",
    "linkedin",
)
SCRAPED = ("indeed", "linkedin")
# One request returns the whole board, so a limit cannot save a request here,
# only lose jobs. These are fetched whole and capped after the Dutch filter.
WHOLE_BOARD = ("recruitee", "greenhouse")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--config", type=Path, default=ROOT / "sources.toml")
    parser.add_argument("--boards", type=Path, default=ROOT / "boards.toml")
    parser.add_argument("--source", choices=SOURCES, help="only this source")
    parser.add_argument(
        "--limit",
        type=int,
        default=100,
        help="at most this many Dutch vacancies per company or search",
    )
    parser.add_argument("--all-countries", action="store_true")
    parser.add_argument(
        "--no-scope",
        action="store_true",
        help="store every Dutch vacancy, not only the [scope] in sources.toml",
    )
    args = parser.parse_args()

    config = load_config(args.config, args.boards)
    markers = config.get("netherlands", {}).get("markers", [])
    store = VacancyStore(RAW_DIR)
    wanted = [args.source] if args.source else SOURCES
    gate = Gate(
        FetchState.load(STATE_PATH), Rules.from_config(config.get("politeness", {}))
    )
    print_cooling_down(gate)
    scope = None if args.no_scope else Scope.from_config(config["scope"])
    sightings = Sightings.load(SIGHTINGS_PATH)
    now = datetime.now(UTC)

    print(
        f"{'source':<12} {'what':<30} {'listed':>6} {'kept':>5} {'dutch':>6} "
        f"{'fits':>5} {'new':>5} {'known':>6} {'dup':>4} {'gone':>5}"
    )
    report = RunReport()
    with new_client(transport=PoliteTransport(gate)) as client:
        for source_name in wanted:
            built = list(
                build_sources(source_name, config, client, store, scope, sightings)
            )
            if not built and source_name in SCRAPED:
                print(f"{source_name:<12} {'(disabled in sources.toml)':<30}")
            every_board_answered = bool(built)
            for label, source in built:
                run = report.add(SearchRun(source_name, label[:30]))
                if not fetch_into(
                    run,
                    source,
                    gate,
                    config,
                    args,
                    store,
                    markers,
                    scope,
                    sightings=sightings,
                    now=now,
                ):
                    every_board_answered = False
                    print(
                        f"{run.source:<12} {run.search:<30} {run.status}: {run.detail}"
                    )
                    continue
                fits = run.dutch - run.out_of_scope
                print(
                    f"{run.source:<12} {run.search:<30} {run.listed:>6} {run.kept:>5} "
                    f"{run.dutch:>6} {fits:>5} {run.stored:>5} {run.known:>6} "
                    f"{run.duplicate:>4} {run.closed:>5}"
                )
                if run.capped:
                    print(f"{'':<12} --limit left out {run.capped} Dutch vacancies")
                if run.incomplete:  # a listing that stopped short closes nothing
                    every_board_answered = False
                    print(f"{'':<12} listing incomplete: nothing on it was closed")
            # A job stored before sightings began has no board on record, so no
            # single board can close it; every board of the source together can.
            if source_name in BOARD_SOURCES and every_board_answered:
                stored = store.existing_keys(source_name)
                closed = sightings.close_unseen(source_name, stored, now)
                if closed:
                    report.closed_unlisted[source_name] = closed
                    print(f"{'':<12} {closed} stored jobs no board lists any more")

    gate.finish()  # a site that answered all night is forgiven its old refusals
    sightings.save()
    report.requests = dict(gate.requests)
    asked = ", ".join(f"{site} {n}" for site, n in sorted(gate.requests.items()))
    print(f"\nrequests per site: {asked or 'none'}")
    total = sum(len(store.load(name)) for name in store.sources())
    path = report.write(RUNS_DIR)
    print(
        f"\n{total} vacancies stored in {RAW_DIR.relative_to(ROOT)}/ (never committed)"
    )
    print(f"report: {path.relative_to(ROOT)}")
    if report.healthy():
        return 0
    print("\nthis run needs a look:")
    for problem in report.problems():
        print(f"  - {problem}")
    return 1


def fetch_into(
    run: SearchRun,
    source,
    gate: Gate,
    config: dict,
    args: argparse.Namespace,
    store: VacancyStore,
    markers: list[str],
    scope: Scope | None = None,
    *,
    sightings: Sightings | None = None,
    now: datetime | None = None,
) -> bool:
    """Run one search and write the result into `run`. False if it broke.

    Every way a fetch can end up gets its own status, because "stored nothing"
    on its own says nothing: it is the normal answer for a quiet week and the
    only answer a broken source gives.

    The API sources are gated inside the client. The scraped ones are gated
    here, once per search: JobSpy sends its own requests, and the search is the
    only part of that traffic we can see.
    """
    limit = source_limit(config, run.source, args.limit)
    site = getattr(source, "site", None)  # set on the scraped sources only
    try:
        if site:
            gate.ask(site)
        # Scraped sources and the aggregator make fewer requests for a lower
        # limit, so they get it here. A board does not, so it gets it below.
        vacancies = source.fetch(limit=None if run.source in WHOLE_BOARD else limit)
    except Refused as err:
        run.status, run.detail = err.status, err.detail
        return False
    except RateLimited as err:
        wait = f"{err.retry_after / 60:.0f} min" if err.retry_after else "unknown"
        run.status, run.detail = "rate_limited", f"retry in {wait}"
        return False
    except LikelyThrottled as err:
        if site:
            gate.refused(site, "descriptions stopped arriving")
        run.status, run.detail = "throttled", str(err)
        return False
    except ScrapeTimeout as err:
        if site:
            gate.failed(site)
        run.status, run.detail = "timeout", str(err)
        return False
    except httpx.HTTPError as err:
        run.status, run.detail = "failed", type(err).__name__
        return False
    if site:
        gate.answered(site)

    stats = getattr(source, "stats", None)  # the scraped sources count as they go
    run.listed = stats.listed if stats else len(vacancies)
    run.dropped_no_text = stats.dropped_no_text if stats else 0
    run.dropped_invalid = stats.dropped_invalid if stats else 0
    run.kept = len(vacancies)
    # Jobs a source never fetched the text of, because they were already stored
    # or outside the scope (SmartRecruiters, LinkedIn): Dutch all the same.
    skipped_known = getattr(stats, "skipped_known", 0)
    skipped_scope = getattr(stats, "out_of_scope", 0)

    # Filter first, cap second: the other way round kept 29 of Adyen's 56
    # Dutch vacancies (sources/netherlands.py). The scope is a filter too.
    selection = select(
        vacancies, markers, limit=limit, all_countries=args.all_countries, scope=scope
    )
    run.dutch = selection.dutch + skipped_known + skipped_scope
    run.capped = selection.capped
    run.out_of_scope = selection.out_of_scope + skipped_scope
    run.left_out = [*getattr(stats, "left_out", []), *selection.left_out][:25]
    result = store.add(selection.kept)
    run.stored, run.duplicate = result.stored, result.duplicate
    run.known = result.known + skipped_known
    if not run.listed:
        run.status = "empty"

    if sightings is not None and scope is not None and hasattr(source, "known_keys"):
        # A source that pays a request per page: what it read tonight and did
        # not store is not read again under this scope. Capped pages are not
        # among them; the cap left them unjudged.
        left_out = {
            v.key
            for v in vacancies
            if not (args.all_countries or is_dutch(v, markers)) or not scope.keep(v)
        }
        sightings.pass_over(left_out | set(result.twins), scope.fingerprint, now)
    if sightings is not None:
        # What the source listed, fetched or not, is what is still open.
        listed = getattr(stats, "listed_keys", None) or {v.key for v in vacancies}
        board = getattr(source, "board", None)  # the employer boards only
        now = now or datetime.now(UTC)
        run.reopened = sightings.seen(listed, board, now)
        # A job we hold under another source's key was listed here too.
        sightings.seen_elsewhere(set(result.twins.values()), now)
        # A board that listed only part of its jobs (Workday past a page limit)
        # says nothing about the rest, so it closes nothing.
        run.incomplete = not getattr(stats, "complete", True)
        if board is not None and not run.incomplete:
            run.closed = sightings.close_missing(board, run.source, listed, now)
    return True


def source_limit(config: dict, name: str, fallback: int) -> int:
    """Scraped sources set their own ceiling in sources.toml; --limit can only
    make a run smaller, never bigger than what the config allows."""
    settings = config.get(name)
    # A source with several companies is a list of tables ([[recruitee]]); only
    # the single-table sources ([indeed]) carry a ceiling of their own.
    configured = (
        settings.get("results_per_search") if isinstance(settings, dict) else None
    )
    return min(fallback, configured) if configured else fallback


def build_sources(
    name: str,
    config: dict,
    client: httpx.Client,
    store: VacancyStore,
    scope: Scope | None = None,
    sightings: Sightings | None = None,
):
    def known(source: str) -> set[str]:
        """Pages that cost no request: stored, or read and left out under this
        very scope (sightings.pass_over). A changed scope reads them again."""
        keys = store.existing_keys(source)
        if sightings is not None and scope is not None:
            keys |= sightings.passed_over(source, scope.fingerprint)
        return keys

    if name == "recruitee":
        for entry in config.get("recruitee", []):
            yield entry["slug"], RecruiteeSource(entry["slug"], client)
    elif name == "greenhouse":
        for entry in config.get("greenhouse", []):
            yield entry["slug"], GreenhouseSource(entry["slug"], client)
    elif name == "careersite":
        known_pages = known("careersite")
        for entry in config.get("careersite", []):
            yield (
                entry["site"],
                CareerSiteSource(
                    entry["site"],
                    entry["sitemap"],
                    entry["pattern"],
                    client,
                    scope=scope,
                    known_keys=known_pages,
                    place_in_url=entry.get("place_in_url", False),
                ),
            )
    elif name == "overheid":
        settings = config.get("overheid", {})
        if settings.get("enabled"):
            yield (
                "werkenbijdeoverheid.nl",
                OverheidSource(
                    client,
                    sitemap=settings.get("sitemap", SITEMAP),
                    scope=scope,
                    known_keys=known("overheid"),
                ),
            )
    elif name == "smartrecruiters":
        known_postings = known("smartrecruiters")
        for entry in config.get("smartrecruiters", []):
            source = SmartRecruitersSource(
                entry["company"], client, scope=scope, known_keys=known_postings
            )
            yield entry["company"], source
    elif name == "workday":
        known_jobs = known("workday")
        robots: dict = {}  # robots.txt read once per host, shared by its sites
        for entry in config.get("workday", []):
            source = WorkdaySource(
                entry["board"],
                client,
                scope=scope,
                known_keys=known_jobs,
                robots=robots,
            )
            yield entry["board"], source
    elif name == "indeed":
        settings = config.get("indeed", {})
        if not settings.get("enabled"):
            return
        for search in settings.get("searches", []):
            yield (
                f"{search['term']} in {search['city']}",
                IndeedSource(
                    search["term"],
                    search["city"],  # country_indeed says which Indeed to ask
                    country=settings.get("country", "netherlands"),
                    distance_km=settings.get("distance_km", 25),
                    hours_old=settings.get("hours_old", 72),
                ),
            )
    elif name == "linkedin":
        settings = config.get("linkedin", {})
        if not settings.get("enabled"):
            return
        known = store.existing_keys("linkedin")  # these cost no request at all
        for search in settings.get("searches", []):
            yield (
                f"{search['term']} in {search['city']}",
                LinkedInSource(
                    search["term"],
                    f"{search['city']}, Netherlands",  # guest search is free text
                    client,
                    distance_km=settings.get("distance_km", 25),
                    hours_old=settings.get("hours_old", 72),
                    known_keys=known,
                    max_descriptions=settings.get("max_descriptions_per_run", 40),
                    delay_seconds=settings.get("delay_seconds", 2.5),
                ),
            )
    elif name == "jobdataapi":
        settings = config.get("jobdataapi", {})
        if settings.get("enabled"):
            yield (
                "aggregator",
                JobDataApiSource(
                    client,
                    country=settings.get("country", "NL"),
                    max_age_days=settings.get("max_age_days", 7),
                    filters=tuple({"title": title} for title in settings["titles"]),
                ),
            )
    elif name == "eures":
        settings = config.get("eures", {})
        if not settings.get("enabled"):
            return
        # The place half of the scope, asked of EURES itself (NUTS 2024 codes).
        regions = nuts_codes(scope.chosen) if scope else ["nl"]
        for title in settings.get("titles", []):
            yield (
                title,
                EuresSource(
                    client,
                    keyword=title,
                    locations=regions,
                    max_age_days=settings.get("max_age_days", 60),
                    pages=settings.get("pages_per_search", 2),
                ),
            )


def print_cooling_down(gate: Gate) -> None:
    """Say up front which sites this run will leave alone, and why."""
    now = datetime.now(UTC)
    for site, entry in sorted(gate.state.sites.items()):
        if entry.blocked_until > now:
            print(
                f"not asking {site} until {entry.blocked_until:%Y-%m-%d %H:%M} UTC: "
                f"{entry.reason} ({entry.strikes}x in a row)"
            )


if __name__ == "__main__":
    sys.exit(main())
