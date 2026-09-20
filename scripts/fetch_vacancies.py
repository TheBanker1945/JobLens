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
"""

import argparse
import sys
import time
import tomllib
from pathlib import Path

import httpx

from joblens.sources.base import Vacancy
from joblens.sources.greenhouse import GreenhouseSource
from joblens.sources.http import RateLimited, new_client
from joblens.sources.jobdataapi import JobDataApiSource
from joblens.sources.recruitee import RecruiteeSource
from joblens.sources.scraped import IndeedSource, LinkedInSource, ScrapeError
from joblens.sources.store import VacancyStore

ROOT = Path(__file__).parent.parent
RAW_DIR = ROOT / "data" / "raw" / "vacancies"
SOURCES = ("recruitee", "greenhouse", "jobdataapi", "indeed", "linkedin")
SCRAPED = ("indeed", "linkedin")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--config", type=Path, default=ROOT / "sources.toml")
    parser.add_argument("--source", choices=SOURCES, help="only this source")
    parser.add_argument("--limit", type=int, default=100, help="per company/source")
    parser.add_argument("--all-countries", action="store_true")
    args = parser.parse_args()

    config = tomllib.loads(args.config.read_text(encoding="utf-8"))
    markers = config.get("netherlands", {}).get("markers", [])
    delay = config.get("delay_seconds", 1.5)
    store = VacancyStore(RAW_DIR)
    wanted = [args.source] if args.source else SOURCES

    print(
        f"{'source':<12} {'what':<30} {'fetched':>7} {'dutch':>6} "
        f"{'new':>5} {'known':>6}"
    )
    with new_client() as client:
        for source_name in wanted:
            built = list(build_sources(source_name, config, client, store))
            if not built and source_name in SCRAPED:
                print(f"{source_name:<12} {'(disabled in sources.toml)':<30}")
            for label, source in built:
                label = label[:30]  # keeps the table lined up
                limit = source_limit(config, source_name, args.limit)
                try:
                    vacancies = source.fetch(limit=limit)
                except RateLimited as err:
                    wait = f"{err.retry_after / 60:.0f} min" if err.retry_after else "?"
                    print(
                        f"{source_name:<12} {label:<30} rate limited, retry in {wait}"
                    )
                    continue
                except ScrapeError as err:
                    print(f"{source_name:<12} {label:<30} {err}")
                    continue
                except httpx.HTTPError as err:
                    print(f"{source_name:<12} {label:<30} failed: {type(err).__name__}")
                    continue

                dutch = (
                    vacancies
                    if args.all_countries
                    else filter_dutch(vacancies, markers)
                )
                stored, skipped = store.add(dutch)
                print(
                    f"{source_name:<12} {label:<30} {len(vacancies):>7} "
                    f"{len(dutch):>6} {stored:>5} {skipped:>6}"
                )
                time.sleep(delay)  # be polite

    total = sum(len(store.load(name)) for name in SOURCES)
    print(
        f"\n{total} vacancies stored in {RAW_DIR.relative_to(ROOT)}/ (never committed)"
    )
    return 0


def source_limit(config: dict, name: str, fallback: int) -> int:
    """Scraped sources set their own ceiling in sources.toml; --limit can only
    make a run smaller, never bigger than what the config allows."""
    configured = config.get(name, {}).get("results_per_search")
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


def filter_dutch(vacancies: list[Vacancy], markers: list[str]) -> list[Vacancy]:
    """Keep vacancies in the Netherlands. Greenhouse boards are worldwide and give
    no country, so we fall back to matching the location text."""
    return [v for v in vacancies if is_dutch(v, markers)]


def is_dutch(vacancy: Vacancy, markers: list[str]) -> bool:
    if (vacancy.country or "").upper() in ("NL", "NLD", "NETHERLANDS"):
        return True
    place = f"{vacancy.city or ''} {location_text(vacancy)}".lower()
    return any(marker.lower() in place for marker in markers)


def location_text(vacancy: Vacancy) -> str:
    """The location as the source wrote it: a string, or {"name": "Amsterdam"}."""
    location = vacancy.raw.get("location")
    if isinstance(location, dict):
        return str(location.get("name", ""))
    return str(location or "")


if __name__ == "__main__":
    sys.exit(main())
