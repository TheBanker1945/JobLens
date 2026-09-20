"""Make the stored vacancies searchable: extract their fields, then embed them.

scripts/fetch_vacancies.py leaves raw vacancy texts in data/raw/. This script
does the two steps that hand them to the AI side of JobLens:

1. **Extraction.** Every vacancy without details gets them, using the model from
   evals/extraction.toml (gemini-3.8-flash by default, the winner of the 1.5
   eval). Results are cached per vacancy in data/raw/extracted/, so the second
   run over the same vacancies costs nothing and no API calls.
2. **Embedding.** The document for each vacancy -- by default a structured
   summary followed by the full text -- is embedded once into data/cache/, so
   searching afterwards is instant and free. The cache is keyed on the
   embedding model, so two models never share a vector.

A vacancy that already has details is skipped, whichever model produced them;
--reextract does them again with the model you chose.

Usage:
    uv run python scripts/index_vacancies.py
    uv run python scripts/index_vacancies.py --source indeed --limit 20
    uv run python scripts/index_vacancies.py --style raw --skip-embedding
"""

import argparse
import sys
from pathlib import Path

import httpx
import openai
from dotenv import load_dotenv

from joblens.config import load_llm_settings
from joblens.embeddings.client import EmbeddingClient
from joblens.embeddings.documents import STYLES
from joblens.embeddings.index import VacancyIndex
from joblens.embeddings.store import CachedEmbedder
from joblens.evals.runner import load_configs
from joblens.extraction.extract import ExtractionError, extract_vacancy
from joblens.extraction.store import DetailsStore, ExtractedVacancy
from joblens.llm.client import LLMClient
from joblens.sources.store import VacancyStore

ROOT = Path(__file__).parent.parent
RAW_DIR = ROOT / "data" / "raw" / "vacancies"
EXTRACTED_DIR = ROOT / "data" / "raw" / "extracted"
CACHE_DIR = ROOT / "data" / "cache"
DEFAULT_RUN = "gemini-3.8-flash"  # the default for vacancy extraction (CLAUDE.md)
FLUSH_EVERY = 10  # write to disk this often, so a crash costs at most 10 extractions


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--source", help="only this source")
    parser.add_argument("--limit", type=int, help="at most this many new extractions")
    parser.add_argument("--run", default=DEFAULT_RUN, help="run name in the config")
    parser.add_argument("--style", default="structured_raw", choices=STYLES)
    parser.add_argument("--skip-embedding", action="store_true")
    parser.add_argument(
        "--reextract", action="store_true", help="redo vacancies that have details"
    )
    args = parser.parse_args()

    load_dotenv()
    configs = {c.name: c for c in load_configs(ROOT / "evals" / "extraction.toml")}
    if args.run not in configs:
        print(f"Unknown run {args.run!r}. Available: {', '.join(configs)}")
        return 1
    config = configs[args.run]

    store = VacancyStore(RAW_DIR)
    details_store = DetailsStore(EXTRACTED_DIR)
    sources = [args.source] if args.source else store.sources()
    if not sources:
        print(f"No vacancies in {RAW_DIR.relative_to(ROOT)}/ yet. Fetch some first.")
        return 1

    print(f"extracting with {config.model} (mode: {config.mode})")
    print(f"{'source':<12} {'stored':>7} {'known':>6} {'new':>5} {'failed':>7}")
    budget = args.limit
    tokens = [0, 0]
    failures: list[str] = []

    with LLMClient(config.settings()) as client:
        for source in sources:
            vacancies = store.load(source)
            cached = details_store.load(source)
            todo = [v for v in vacancies if args.reextract or v.key not in cached]
            if budget is not None:
                todo = todo[:budget]
            done, failed, pending = 0, 0, []

            for vacancy in todo:
                try:
                    result = extract_vacancy(vacancy.text, client, mode=config.mode)
                except ExtractionError as err:
                    failed += 1
                    failures.append(f"{vacancy.key}: {err}")
                    continue
                pending.append(
                    ExtractedVacancy(
                        key=vacancy.key, model=config.model, details=result.details
                    )
                )
                tokens[0] += result.prompt_tokens
                tokens[1] += result.output_tokens
                done += 1
                if len(pending) >= FLUSH_EVERY:
                    details_store.add(source, pending)
                    pending = []
                progress(f"  {source}: {done}/{len(todo)}")

            details_store.add(source, pending)
            progress("")  # the counter has served its purpose
            if budget is not None:
                budget -= done + failed
            print(
                f"{source:<12} {len(vacancies):>7} {len(cached):>6} "
                f"{done:>5} {failed:>7}"
            )

    print(f"\n{tokens[0]} prompt + {tokens[1]} output tokens{price(config, tokens)}")
    for failure in failures[:5]:
        print(f"  failed: {failure[:110]}")

    if args.skip_embedding:
        return 0
    return embed(store, details_store, sources, args.style)


def embed(
    store: VacancyStore, details_store: DetailsStore, sources: list[str], style: str
) -> int:
    """Embed every vacancy that has details, filling the cache search reads."""
    settings = load_llm_settings(prefix="EMBED")
    cache = CACHE_DIR / f"embeddings-{settings.model.replace(':', '-')}.json"
    vacancies = [vacancy for source in sources for vacancy in store.load(source)]
    details = {
        key: record.details for key, record in details_store.load_all(sources).items()
    }

    try:
        with EmbeddingClient(settings) as client:
            embedder = CachedEmbedder(client, cache)
            index = VacancyIndex.build(vacancies, details, embedder, style=style)
            index.vectors()
    except (httpx.ConnectError, openai.APIConnectionError):
        print(f"Cannot reach {settings.base_url}. Is the embedding server running?")
        return 1

    for vacancy in index.oversized():
        print(
            f"  warning: {vacancy.key} is longer than the embedder's window; "
            "its tail was not embedded"
        )
    missing = len(vacancies) - len(index)
    print(
        f"embedded {len(index)} vacancies as {style} with {settings.model} "
        f"({embedder.hits} from cache, {embedder.misses} new)"
        + (f"; {missing} still have no extracted details" if missing else "")
    )
    return 0


def progress(line: str) -> None:
    """Overwrite one line in place, so a long run shows where it is.

    Only on a terminal: written to a file, a carriage return leaves every
    counter on one endless line, and the nightly run logs to a file.
    """
    if sys.stdout.isatty():
        print(line.ljust(40), end="\r", flush=True)


def price(config, tokens: list[int]) -> str:
    """What the run cost, when the config knows the model's prices."""
    if config.usd_per_m_input is None or config.usd_per_m_output is None:
        return ""
    usd = (
        tokens[0] * config.usd_per_m_input + tokens[1] * config.usd_per_m_output
    ) / 1_000_000
    return f" (about ${usd:.3f})"


if __name__ == "__main__":
    sys.exit(main())
