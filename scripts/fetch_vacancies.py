"""Fetch real vacancies from the public sources in sources.toml into data/raw/.

data/raw/ is never committed: vacancy texts are someone else's copyright, and we
only need them locally to test extraction and search.

Sources come in two kinds. The API sources (Recruitee, Greenhouse, jobdataapi)
publish JSON meant to be read. The scraped ones (Indeed, LinkedIn) do not, need
the optional dependency group, and can fail in quieter ways -- see
src/joblens/sources/scraped.py.

Usage:
    uv run python scripts/fetch_vacancies.py                    # all sources
    uv run python scripts/fetch_vacancies.py --source recruitee --limit 20
    uv run python scripts/fetch_vacancies.py --all-countries    # skip the NL filter

    uv sync --group scrape                                      # once, for these:
    uv run python scripts/fetch_vacancies.py --source indeed

Every run writes a report to data/raw/runs/ and exits non-zero when something
looks wrong (a source that found nothing at all, descriptions that never
arrived, a board refusing us), so a scheduled run can tell us it went bad.
"""

import argparse
import sys
import time
import tomllib
from pathlib import Path

import httpx

from joblens.sources.greenhouse import GreenhouseSource
from joblens.sources.http import RateLimited, new_client
from joblens.sources.jobdataapi import JobDataApiSource
from joblens.sources.netherlands import select
from joblens.sources.recruitee import RecruiteeSource
from joblens.sources.report import RunReport, SearchRun
from joblens.sources.scraped import (
    IndeedSource,
    LikelyThrottled,
    LinkedInSource,
    ScrapeTimeout,
)
from joblens.sources.store import VacancyStore

ROOT = Path(__file__).parent.parent
RAW_DIR = ROOT / "data" / "raw" / "vacancies"
RUNS_DIR = ROOT / "data" / "raw" / "runs"
SOURCES = ("recruitee", "greenhouse", "jobdataapi", "indeed", "linkedin")
SCRAPED = ("indeed", "linkedin")
# One request returns the whole board, so a limit cannot save a request here,
# only lose jobs. These are fetched whole and capped after the Dutch filter.
WHOLE_BOARD = ("recruitee", "greenhouse")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--config", type=Path, default=ROOT / "sources.toml")
    parser.add_argument("--source", choices=SOURCES, help="only this source")
    parser.add_argument(
        "--limit",
        type=int,
        default=100,
        help="at most this many Dutch vacancies per company or search",
    )
    parser.add_argument("--all-countries", action="store_true")
    args = parser.parse_args()

    config = tomllib.loads(args.config.read_text(encoding="utf-8"))
    markers = config.get("netherlands", {}).get("markers", [])
    delay = config.get("delay_seconds", 1.5)
    store = VacancyStore(RAW_DIR)
    wanted = [args.source] if args.source else SOURCES

    print(
        f"{'source':<12} {'what':<30} {'listed':>6} {'kept':>5} {'dutch':>6} "
        f"{'new':>5} {'known':>6} {'dup':>4}"
    )
    report = RunReport()
    with new_client() as client:
        for source_name in wanted:
            built = list(build_sources(source_name, config, client, store))
            if not built and source_name in SCRAPED:
                print(f"{source_name:<12} {'(disabled in sources.toml)':<30}")
            for label, source in built:
                run = report.add(SearchRun(source_name, label[:30]))
                if not fetch_into(run, source, config, args, store, markers):
                    print(
                        f"{run.source:<12} {run.search:<30} {run.status}: {run.detail}"
                    )
                    continue
                print(
                    f"{run.source:<12} {run.search:<30} {run.listed:>6} {run.kept:>5} "
                    f"{run.dutch:>6} {run.stored:>5} {run.known:>6} {run.duplicate:>4}"
                )
                if run.capped:
                    print(f"{'':<12} --limit left out {run.capped} Dutch vacancies")
                time.sleep(delay)  # be polite

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
    config: dict,
    args: argparse.Namespace,
    store: VacancyStore,
    markers: list[str],
) -> bool:
    """Run one search and write the result into `run`. False if it broke.

    Every way a fetch can end up gets its own status, because "stored nothing"
    on its own says nothing: it is the normal answer for a quiet week and the
    only answer a broken source gives.
    """
    limit = source_limit(config, run.source, args.limit)
    try:
        # Scraped sources and the aggregator make fewer requests for a lower
        # limit, so they get it here. A board does not, so it gets it below.
        vacancies = source.fetch(limit=None if run.source in WHOLE_BOARD else limit)
    except RateLimited as err:
        wait = f"{err.retry_after / 60:.0f} min" if err.retry_after else "unknown"
        run.status, run.detail = "rate_limited", f"retry in {wait}"
        return False
    except LikelyThrottled as err:
        run.status, run.detail = "throttled", str(err)
        return False
    except ScrapeTimeout as err:
        run.status, run.detail = "timeout", str(err)
        return False
    except httpx.HTTPError as err:
        run.status, run.detail = "failed", type(err).__name__
        return False

    stats = getattr(source, "stats", None)  # the scraped sources count as they go
    run.listed = stats.listed if stats else len(vacancies)
    run.dropped_no_text = stats.dropped_no_text if stats else 0
    run.dropped_invalid = stats.dropped_invalid if stats else 0
    run.kept = len(vacancies)

    # Filter first, cap second: the other way round kept 29 of Adyen's 56
    # Dutch vacancies (sources/netherlands.py).
    selection = select(
        vacancies, markers, limit=limit, all_countries=args.all_countries
    )
    run.dutch, run.capped = selection.dutch, selection.capped
    result = store.add(selection.kept)
    run.stored, run.known, run.duplicate = result.stored, result.known, result.duplicate
    if not run.listed:
        run.status = "empty"
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


def build_sources(name: str, config: dict, client: httpx.Client, store: VacancyStore):
    if name == "recruitee":
        for entry in config.get("recruitee", []):
            yield entry["slug"], RecruiteeSource(entry["slug"], client)
    elif name == "greenhouse":
        for entry in config.get("greenhouse", []):
            yield entry["slug"], GreenhouseSource(entry["slug"], client)
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
                ),
            )


if __name__ == "__main__":
    sys.exit(main())
