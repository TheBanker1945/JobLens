"""Semantic search over vacancies: rank them by meaning, not by keywords.

Two corpora, the same code:

- samples: the 10 fictional vacancies in data/samples/ -- committed, so everyone
  who clones the repo gets the same results.
- raw:     the real vacancies fetched into data/raw/ -- yours, never committed,
  and only the ones scripts/index_vacancies.py has already extracted.

Usage:
    uv run python scripts/search_vacancies.py "python baan in amsterdam"
    uv run python scripts/search_vacancies.py "zorg voor ouderen" --corpus raw
    uv run python scripts/search_vacancies.py "python baan" --no-instruction
"""

import argparse
import json
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

import httpx
import openai

from joblens.config import load_llm_settings
from joblens.embeddings.client import EmbeddingClient
from joblens.embeddings.documents import STYLES
from joblens.embeddings.index import Match, VacancyIndex
from joblens.embeddings.store import CachedEmbedder
from joblens.extraction.schema import VacancyDetails
from joblens.extraction.store import DetailsStore
from joblens.sources.base import Vacancy
from joblens.sources.store import VacancyStore

ROOT = Path(__file__).parent.parent
SAMPLES = ROOT / "data" / "samples" / "vacancies"
SAMPLE_DETAILS = ROOT / "data" / "samples" / "extracted"
RAW_DIR = ROOT / "data" / "raw" / "vacancies"
EXTRACTED_DIR = ROOT / "data" / "raw" / "extracted"
CACHE_DIR = ROOT / "data" / "cache"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("query")
    parser.add_argument("--corpus", choices=("samples", "raw"), default="samples")
    parser.add_argument("--top", type=int, default=10)
    parser.add_argument("--style", default="structured_raw", choices=STYLES)
    parser.add_argument(
        "--no-instruction",
        action="store_true",
        help="embed the query as plain text, to see what the instruction changes",
    )
    args = parser.parse_args()

    load = load_samples if args.corpus == "samples" else load_raw
    vacancies, details = load()
    if not vacancies:
        print("Nothing to search. Fetch and index some vacancies first.")
        return 1

    settings = load_llm_settings(prefix="EMBED")
    cache = CACHE_DIR / f"embeddings-{settings.model.replace(':', '-')}.json"
    try:
        with EmbeddingClient(settings) as client:
            embedder = CachedEmbedder(client, cache)
            if args.no_instruction:
                embedder = PlainQueries(embedder)
            index = VacancyIndex.build(vacancies, details, embedder, style=args.style)
            start = time.perf_counter()
            matches = index.search(args.query, top_k=args.top)
            took = time.perf_counter() - start
    except (httpx.ConnectError, openai.APIConnectionError):
        print(f"Cannot reach {settings.base_url}. Is the embedding server running?")
        return 1

    skipped = len(vacancies) - len(index)
    print(
        f"model: {settings.model}  |  {len(index)} vacancies as {args.style}"
        + (f" ({skipped} not extracted yet)" if skipped else "")
        + f"  |  searched in {took:.1f}s"
    )
    print(f"query: {args.query!r}{'  (no instruction)' if args.no_instruction else ''}")
    print()
    for position, match in enumerate(matches, 1):
        show(position, match)
    return 0


def load_samples() -> tuple[list[Vacancy], dict[str, VacancyDetails]]:
    """The committed sample texts, as vacancies, with their stored extractions."""
    vacancies, details = [], {}
    for path in sorted(SAMPLES.glob("*.txt")):
        text = path.read_text(encoding="utf-8")
        extracted = SAMPLE_DETAILS / f"{path.stem}.json"
        found = None
        if extracted.exists():
            payload = json.loads(extracted.read_text(encoding="utf-8"))
            found = VacancyDetails.model_validate(payload["details"])
        # A sample is a text file, so the company and city it mentions are only
        # known once the text has been extracted.
        vacancy = Vacancy(
            source="sample",
            source_id=path.stem,
            url=str(path.relative_to(ROOT)),
            title=found.title if found else text.splitlines()[0],
            company=found.company if found else None,
            city=found.city if found else None,
            text=text,
        )
        vacancies.append(vacancy)
        if found:
            details[vacancy.key] = found
    return vacancies, details


def load_raw() -> tuple[list[Vacancy], dict[str, VacancyDetails]]:
    store = VacancyStore(RAW_DIR)
    sources = store.sources()
    records = DetailsStore(EXTRACTED_DIR).load_all(sources)
    vacancies = [vacancy for source in sources for vacancy in store.load(source)]
    return vacancies, {key: record.details for key, record in records.items()}


class PlainQueries:
    """Embeds a query like any other text, so the instruction can be compared."""

    def __init__(self, embedder):
        self.embedder = embedder

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return self.embedder.embed_documents(texts)

    def embed_query(self, query: str) -> list[float]:
        return self.embedder.embed_documents([query])[0]


def show(position: int, match: Match) -> None:
    vacancy = match.vacancy
    where = " · ".join(part for part in (vacancy.company, vacancy.city) if part)
    print(
        f"{position:>2}. {match.score:.3f}  {vacancy.title[:44]:<44} "
        f"{where[:34]:<34} {age(vacancy)}"
    )
    if vacancy.url.startswith("http"):
        print(f"           {vacancy.url}")


def age(vacancy: Vacancy) -> str:
    """How old the vacancy is. Blank when the board did not say when it posted:
    the day we happened to fetch it is not the same thing."""
    if vacancy.posted_at is None:
        return ""
    posted = vacancy.posted_at
    if posted.tzinfo is None:  # some boards give a date without a timezone
        posted = posted.replace(tzinfo=UTC)
    return f"{(datetime.now(UTC) - posted).days}d"


if __name__ == "__main__":
    sys.exit(main())
