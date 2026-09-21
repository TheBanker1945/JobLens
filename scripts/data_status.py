"""How fresh is the data, and did the last update go well?

The app never scrapes while someone is using it, so what a demo shows is only as
good as the last update. This answers, in one screen: how much is stored, how
much of it is searchable, how old the vacancies are, and whether the last run
was healthy.

Exits non-zero when the data is stale or the last run had a problem, so it works
as a check before a demo -- or in cron, if you want a nudge when the nightly run
has quietly stopped happening.

Usage:
    uv run python scripts/data_status.py
    uv run python scripts/data_status.py --max-age-hours 48
"""

import argparse
import sys
from datetime import UTC, datetime
from pathlib import Path

from joblens.config import load_llm_settings
from joblens.embeddings.client import EmbeddingClient
from joblens.embeddings.documents import build_document
from joblens.embeddings.store import CachedEmbedder
from joblens.extraction.store import DetailsStore
from joblens.sources.base import Vacancy
from joblens.sources.report import RunReport
from joblens.sources.store import VacancyStore

ROOT = Path(__file__).parent.parent
RAW_DIR = ROOT / "data" / "raw" / "vacancies"
EXTRACTED_DIR = ROOT / "data" / "raw" / "extracted"
RUNS_DIR = ROOT / "data" / "raw" / "runs"
CACHE_DIR = ROOT / "data" / "cache"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--max-age-hours", type=float, default=36.0)
    parser.add_argument("--style", default="structured_raw")
    args = parser.parse_args()

    store = VacancyStore(RAW_DIR)
    sources = store.sources()
    if not sources:
        print("No vacancies stored yet. Run scripts/fetch_vacancies.py.")
        return 1

    vacancies = {source: store.load(source) for source in sources}
    total = sum(len(group) for group in vacancies.values())
    details = DetailsStore(EXTRACTED_DIR).load_all(sources)

    print(f"{'source':<12} {'stored':>7} {'extracted':>10} {'newest':>8}")
    for source, group in vacancies.items():
        extracted = sum(1 for v in group if v.key in details)
        print(f"{source:<12} {len(group):>7} {extracted:>10} {newest(group):>8}")
    print(f"{'total':<12} {total:>7} {len(details):>10}")

    print(f"\nembedded: {embedded(vacancies, details, args.style)}")

    problems = []
    report = RunReport.latest(RUNS_DIR)
    if report is None:
        problems.append("no fetch run has ever been recorded")
    else:
        finished = datetime.fromisoformat(report["finished_at"])
        hours = (datetime.now(UTC) - finished).total_seconds() / 3600
        print(
            f"last run: {finished:%Y-%m-%d %H:%M} ({hours:.0f}h ago), "
            f"{'healthy' if report['healthy'] else 'WITH PROBLEMS'}, "
            f"{report['totals']['stored']} new vacancies"
        )
        problems += [f"last run: {problem}" for problem in report["problems"]]
        if hours > args.max_age_hours:
            problems.append(
                f"the last update was {hours:.0f}h ago, more than the "
                f"{args.max_age_hours:.0f}h this check allows"
            )

    if not problems:
        print("\nup to date")
        return 0
    print("\nneeds a look:")
    for problem in problems:
        print(f"  - {problem}")
    return 1


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
