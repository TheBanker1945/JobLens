"""How fresh is the data, and did the last update go well?

The app never scrapes while someone is using it, so what a demo shows is only as
good as the last update. This answers, in one screen: how much is stored, how
much of it is searchable, how old the vacancies are, and whether the last run
was healthy.

Exits non-zero when any enabled source is stale or the last run had a problem,
so it works as a check before a demo -- or in cron, if you want a nudge when the
nightly run has quietly stopped happening. Freshness is per source: a manual
`fetch_vacancies.py --source indeed` refreshes Indeed and nothing else.

Usage:
    uv run python scripts/data_status.py
    uv run python scripts/data_status.py --max-age-hours 48
"""

import argparse
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

from joblens.config import load_llm_settings
from joblens.embeddings.client import EmbeddingClient
from joblens.embeddings.documents import build_document
from joblens.embeddings.store import CachedEmbedder
from joblens.extraction.store import DetailsStore
from joblens.sources.base import Vacancy
from joblens.sources.boards import load_config
from joblens.sources.polite import FetchState
from joblens.sources.report import RunReport
from joblens.sources.sightings import Sightings
from joblens.sources.store import VacancyStore

ROOT = Path(__file__).parent.parent
RAW_DIR = ROOT / "data" / "raw" / "vacancies"
EXTRACTED_DIR = ROOT / "data" / "raw" / "extracted"
RUNS_DIR = ROOT / "data" / "raw" / "runs"
STATE_PATH = ROOT / "data" / "raw" / "fetch-state.json"
SIGHTINGS_PATH = ROOT / "data" / "raw" / "sightings.json"
CACHE_DIR = ROOT / "data" / "cache"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--max-age-hours", type=float, default=36.0)
    parser.add_argument("--config", type=Path, default=ROOT / "sources.toml")
    parser.add_argument("--boards", type=Path, default=ROOT / "boards.toml")
    # `structured`, the style the index reads since 3.1. The old default counted
    # vectors for `structured_raw`, which nothing searches any more.
    parser.add_argument("--style", default="structured")
    args = parser.parse_args()

    store = VacancyStore(RAW_DIR)
    sources = store.sources()
    if not sources:
        print("No vacancies stored yet. Run scripts/fetch_vacancies.py.")
        return 1

    vacancies = {source: store.load(source) for source in sources}
    total = sum(len(group) for group in vacancies.values())
    details = DetailsStore(EXTRACTED_DIR).load_all(sources)
    enabled = enabled_sources(load_config(args.config, args.boards))
    fetched = RunReport.last_fetched(RUNS_DIR)
    now = datetime.now(UTC)

    sightings = Sightings.load(SIGHTINGS_PATH)
    open_total = 0
    print(
        f"{'source':<12} {'stored':>7} {'open':>6} {'extracted':>10} {'newest':>8} "
        f"{'fetched':>9}"
    )
    for source, group in vacancies.items():
        extracted = sum(1 for v in group if v.key in details)
        still_open = sum(sightings.is_open(v, now) for v in group)
        open_total += still_open
        when = (
            ago(now - fetched[source])
            if source in fetched
            else "never"
            if source in enabled
            else "-"
        )
        note = "" if source in enabled else "  (disabled in sources.toml)"
        print(
            f"{source:<12} {len(group):>7} {still_open:>6} {extracted:>10} "
            f"{newest(group):>8} {when:>9}{note}"
        )
    print(f"{'total':<12} {total:>7} {open_total:>6} {len(details):>10}")
    print(
        "open: an employer board still lists it; a search job was listed in the "
        "last 7 days or is at most 30 days old (src/joblens/sources/sightings.py)"
    )

    print(f"\nembedded: {embedded(vacancies, details, args.style)}")

    problems = []
    report = RunReport.latest(RUNS_DIR)
    if report is None:
        problems.append("no fetch run has ever been recorded")
    else:
        finished = datetime.fromisoformat(report["finished_at"])
        print(
            f"last run: {finished:%Y-%m-%d %H:%M} ({ago(now - finished)} ago), "
            f"{'healthy' if report['healthy'] else 'WITH PROBLEMS'}, "
            f"{report['totals']['stored']} new vacancies, "
            f"sources: {', '.join(sorted({s['source'] for s in report['searches']}))}"
        )
        problems += [f"last run: {problem}" for problem in report["problems"]]

    # Informational: a site cooling down already shows up as a problem in the
    # run that asked it, or as a stale source below. This says until when.
    for site, entry in sorted(FetchState.load(STATE_PATH).sites.items()):
        if entry.blocked_until > now:
            print(
                f"refusing us: {site}, not asked until "
                f"{entry.blocked_until:%Y-%m-%d %H:%M} UTC "
                f"({entry.reason}, {entry.strikes}x in a row)"
            )

    # Per source, not per run: a run of one source says nothing about the rest.
    for source in sorted(enabled & set(sources)):
        if source not in fetched:
            problems.append(f"{source}: no finished fetch in the stored reports")
            continue
        age = now - fetched[source]
        if age.total_seconds() / 3600 > args.max_age_hours:
            problems.append(
                f"{source}: last fetched {ago(age)} ago, more than the "
                f"{args.max_age_hours:.0f}h this check allows"
            )

    if not problems:
        print("\nup to date")
        return 0
    print("\nneeds a look:")
    for problem in problems:
        print(f"  - {problem}")
    return 1


def enabled_sources(config: dict) -> set[str]:
    """The sources a scheduled fetch asks, read the way fetch_vacancies.py reads
    them: a company board when it lists a company, the rest when enabled."""
    boards = {
        name
        for name in ("recruitee", "greenhouse", "smartrecruiters")
        if config.get(name)
    }
    switched = {
        name
        for name in ("overheid", "jobdataapi", "indeed", "linkedin")
        if config.get(name, {}).get("enabled")
    }
    return boards | switched


def ago(delta: timedelta) -> str:
    hours = delta.total_seconds() / 3600
    if hours < 1:
        return "<1h"
    return f"{hours:.0f}h" if hours < 48 else f"{hours / 24:.0f}d"


def embedded(vacancies: dict[str, list[Vacancy]], details: dict, style: str) -> str:
    """How many documents already have a vector, asking only the local cache."""
    settings = load_llm_settings(prefix="EMBED")
    cache = CACHE_DIR / f"embeddings-{settings.model.replace(':', '-')}.json"
    embedder = CachedEmbedder(EmbeddingClient(settings), cache)
    ready = wanted = 0
    for group in vacancies.values():
        for vacancy in group:
            record = details.get(vacancy.key)
            if record is None:
                continue
            wanted += 1
            document = build_document(vacancy.text, record.details, style)
            ready += embedder.is_cached(document)
    return f"{ready} of {wanted} documents as {style} ({settings.model})"


def newest(group: list[Vacancy]) -> str:
    """Age of the most recently posted vacancy from this source."""
    posted = [v.posted_at for v in group if v.posted_at]
    if not posted:
        return "-"
    latest = max(p if p.tzinfo else p.replace(tzinfo=UTC) for p in posted)
    return f"{(datetime.now(UTC) - latest).days}d"


if __name__ == "__main__":
    sys.exit(main())
