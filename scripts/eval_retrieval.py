"""Measure search quality per variant: which text do we embed, with which model?

Usage:
    uv run python scripts/eval_retrieval.py                     # all variants
    uv run python scripts/eval_retrieval.py --corpus raw        # real vacancies
    uv run python scripts/eval_retrieval.py --only local        # matching names
    uv run python scripts/eval_retrieval.py --split dev --mistakes

Two corpora (joblens.corpus): `samples` is the 10 committed fictional vacancies,
the public test anyone can reproduce; `raw` is the real ones in data/raw/, which
are never committed, so those numbers are only reproducible here. The labelled
queries per corpus live in evals/queries/.

Results are reported per split. --mistakes shows the dev split only: the holdout
split must never be used for choosing a variant.
"""

import argparse
import datetime
import json
import subprocess
import sys
import time
from pathlib import Path

import httpx
import openai
from dotenv import load_dotenv

from joblens.corpus import NAMES, Corpus, load_corpus
from joblens.embeddings.client import EmbeddingClient
from joblens.embeddings.documents import build_document
from joblens.embeddings.store import CachedEmbedder
from joblens.evals.retrieval import (
    Query,
    QueryResult,
    RetrievalConfig,
    RetrievalResult,
    load_configs,
    load_queries,
    rank_all,
)

ROOT = Path(__file__).parent.parent
QUERIES_DIR = ROOT / "evals" / "queries"
CACHE_DIR = ROOT / "data" / "cache"
RESULTS_DIR = ROOT / "evals" / "results"
SPLITS = ("dev", "holdout")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--config", type=Path, default=ROOT / "evals/retrieval.toml")
    parser.add_argument("--corpus", choices=NAMES, default="samples")
    parser.add_argument("--only", help="only variants whose name contains this text")
    parser.add_argument("--split", choices=SPLITS, help="report one split only")
    parser.add_argument(
        "--mistakes", action="store_true", help="dev queries that missed"
    )
    parser.add_argument("--no-save", action="store_true")
    args = parser.parse_args()

    load_dotenv()
    queries_path = QUERIES_DIR / f"{args.corpus}.json"
    if not queries_path.exists():
        print(
            f"No labelled queries for the {args.corpus} corpus yet "
            f"({queries_path.relative_to(ROOT)}).\n"
            "Label some first: uv run python scripts/label_queries.py "
            f"--corpus {args.corpus}"
        )
        return 1
    queries = load_queries(queries_path)
    corpus = load_corpus(args.corpus)
    usable = corpus.extracted()
    configs = [
        c for c in load_configs(args.config) if not args.only or args.only in c.name
    ]
    print(
        f"corpus {corpus.name}: {len(queries)} queries x {len(usable)} vacancies "
        f"x {len(configs)} variants"
    )
    if len(usable) < len(corpus):
        missing = len(corpus) - len(usable)
        print(f"  {missing} vacancies left out: not extracted yet")
    warn_about_unknown_keys(queries, usable)
    print()

    results = []
    try:
        for config in configs:
            print(f"running {config.name} ...", flush=True)
            results.append(run_variant(config, usable, queries))
    except (httpx.ConnectError, openai.APIConnectionError) as err:
        print(f"Cannot reach the embedding server: {err}")
        return 1

    for split in [args.split] if args.split else SPLITS:
        split_results = [r.for_split(split) for r in results]
        print(f"\n===== {split} ({len(split_results[0].results)} queries) =====")
        print_summary(split_results)
    if args.mistakes:
        print_mistakes([r.for_split("dev") for r in results], usable)
    if not args.no_save:
        print(f"\nsaved: {save(results, corpus.name).relative_to(ROOT)}")
    return 0


def warn_about_unknown_keys(queries: list[Query], corpus: Corpus) -> None:
    """Labelled vacancies that are no longer in the corpus.

    A query file outlives the corpus it was labelled against: vacancies are
    re-fetched, a source is dropped, a store is rebuilt. A relevant key that no
    longer exists can never be found, so it silently drags every variant down --
    better to hear about it than to wonder why the numbers fell.
    """
    known = corpus.by_key()
    unknown = {key for q in queries for key in q.relevant if key not in known}
    if unknown:
        print(f"  WARNING: {len(unknown)} labelled vacancies are gone from the corpus")
        for key in sorted(unknown):
            print(f"    {key}")


def run_variant(
    config: RetrievalConfig, corpus: Corpus, queries: list[Query]
) -> RetrievalResult:
    documents = [
        build_document(v.text, corpus.details.get(v.key), config.style)
        for v in corpus.vacancies
    ]
    cache_path = CACHE_DIR / f"embeddings-{config.model.replace(':', '-')}.json"
    start = time.perf_counter()
    with EmbeddingClient(config.settings()) as client:
        embedder = CachedEmbedder(client, cache_path)
        doc_vectors = embedder.embed_documents(documents)
        query_vectors = [
            embedder.embed_query(q.query)
            if config.instruction
            else embedder.embed_documents([q.query])[0]
            for q in queries
        ]
    seconds = time.perf_counter() - start

    rankings = rank_all(query_vectors, doc_vectors)
    return RetrievalResult(
        variant=config.name,
        style=config.style,
        model=config.model,
        seconds=seconds,
        api_calls=embedder.misses,
        results=[
            QueryResult(
                query=query.query,
                split=query.split,
                ranked=[corpus.vacancies[i].key for i in ranking],
                relevant=query.relevant,
            )
            for query, ranking in zip(queries, rankings, strict=True)
        ],
    )


def print_summary(results: list[RetrievalResult]) -> None:
    print(
        f"\n{'variant':<34} {'style':<14} {'hit@1':>6} "
        f"{'recall@3':>9} {'recall@10':>10} {'MRR':>6}"
    )
    for r in results:
        print(
            f"{r.variant:<34} {r.style:<14} {r.hit_at_1:>6.0%} "
            f"{r.recall_at_3:>9.0%} {r.recall_at_10:>10.0%} {r.mrr:>6.2f}"
        )


def print_mistakes(results: list[RetrievalResult], corpus: Corpus) -> None:
    known = corpus.by_key()

    def title(key: str) -> str:
        vacancy = known.get(key)
        return vacancy.title if vacancy else f"<{key} is gone>"

    for r in results:
        misses = [q for q in r.results if q.recall_at(3) < 1]
        print(f"\n--- dev queries missing a relevant vacancy in the top 3: {r.variant}")
        for q in misses:
            position = next(
                (i for i, key in enumerate(q.ranked, 1) if key in q.relevant), None
            )
            print(f"  {q.query}")
            print(f"      want:  {'; '.join(title(key) for key in q.relevant)}")
            print(f"      got:   {title(q.ranked[0])}")
            print(f"      first relevant at #{position}")
        if not misses:
            print("  (none)")


def save(results: list[RetrievalResult], corpus: str) -> Path:
    commit = subprocess.run(
        ["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True
    ).stdout.strip()
    now = datetime.datetime.now()
    report = {
        "date": now.isoformat(timespec="seconds"),
        "git_commit": commit,
        "corpus": corpus,
        "runs": [
            {
                "summary": {
                    split: {
                        "hit_at_1": r.for_split(split).hit_at_1,
                        "recall_at_3": r.for_split(split).recall_at_3,
                        "recall_at_10": r.for_split(split).recall_at_10,
                        "mrr": r.for_split(split).mrr,
                    }
                    for split in SPLITS
                },
                **r.model_dump(mode="json"),
            }
            for r in results
        ],
    }
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    path = RESULTS_DIR / f"{now:%Y-%m-%d_%H%M}_retrieval_{corpus}.json"
    path.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n")
    return path


if __name__ == "__main__":
    sys.exit(main())
