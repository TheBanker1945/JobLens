"""Measure search quality per variant: which text do we embed, with which model?

Usage:
    uv run python scripts/eval_retrieval.py                     # all variants
    uv run python scripts/eval_retrieval.py --only local        # matching names
    uv run python scripts/eval_retrieval.py --split dev --mistakes

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

from joblens.embeddings.client import EmbeddingClient
from joblens.embeddings.documents import build_document
from joblens.embeddings.store import CachedEmbedder
from joblens.evals.retrieval import (
    QueryResult,
    RetrievalConfig,
    RetrievalResult,
    load_configs,
    load_queries,
    load_vacancies,
    rank_all,
)
from joblens.extraction.schema import VacancyDetails

ROOT = Path(__file__).parent.parent
SAMPLES = ROOT / "data" / "samples"
CACHE_DIR = ROOT / "data" / "cache"
RESULTS_DIR = ROOT / "evals" / "results"
SPLITS = ("dev", "holdout")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--config", type=Path, default=ROOT / "evals/retrieval.toml")
    parser.add_argument("--only", help="only variants whose name contains this text")
    parser.add_argument("--split", choices=SPLITS, help="report one split only")
    parser.add_argument(
        "--mistakes", action="store_true", help="dev queries that missed"
    )
    parser.add_argument("--no-save", action="store_true")
    args = parser.parse_args()

    load_dotenv()
    queries = load_queries(SAMPLES / "queries.json")
    vacancies = load_vacancies(SAMPLES / "vacancies", SAMPLES / "extracted")
    configs = [
        c for c in load_configs(args.config) if not args.only or args.only in c.name
    ]
    print(
        f"{len(queries)} queries x {len(vacancies)} vacancies "
        f"x {len(configs)} variants\n"
    )

    results = []
    try:
        for config in configs:
            print(f"running {config.name} ...", flush=True)
            results.append(run_variant(config, vacancies, queries))
    except (httpx.ConnectError, openai.APIConnectionError) as err:
        print(f"Cannot reach the embedding server: {err}")
        return 1

    for split in [args.split] if args.split else SPLITS:
        split_results = [r.for_split(split) for r in results]
        print(f"\n===== {split} ({len(split_results[0].results)} queries) =====")
        print_summary(split_results)
    if args.mistakes:
        print_mistakes([r.for_split("dev") for r in results])
    if not args.no_save:
        print(f"\nsaved: {save(results).relative_to(ROOT)}")
    return 0


def run_variant(config: RetrievalConfig, vacancies, queries) -> RetrievalResult:
    documents = [
        build_document(
            v.text,
            VacancyDetails.model_validate(v.details) if v.details else None,
            config.style,
        )
        for v in vacancies
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
                ranked=[vacancies[i].name for i in ranking],
                relevant=query.relevant,
            )
            for query, ranking in zip(queries, rankings, strict=True)
        ],
    )


def print_summary(results: list[RetrievalResult]) -> None:
    print(f"\n{'variant':<34} {'style':<12} {'hit@1':>6} {'recall@3':>9} {'MRR':>6}")
    for r in results:
        print(
            f"{r.variant:<34} {r.style:<12} {r.hit_at_1:>6.0%} "
            f"{r.recall_at_3:>9.0%} {r.mrr:>6.2f}"
        )


def print_mistakes(results: list[RetrievalResult]) -> None:
    for r in results:
        misses = [q for q in r.results if q.recall_at(3) < 1]
        print(f"\n--- dev queries missing a relevant vacancy in the top 3: {r.variant}")
        for q in misses:
            position = next(
                (i for i, name in enumerate(q.ranked, 1) if name in q.relevant), None
            )
            print(
                f"  {q.query:<45} want={','.join(q.relevant)} "
                f"first relevant at #{position}  top1={q.ranked[0]}"
            )
        if not misses:
            print("  (none)")


def save(results: list[RetrievalResult]) -> Path:
    commit = subprocess.run(
        ["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True
    ).stdout.strip()
    now = datetime.datetime.now()
    report = {
        "date": now.isoformat(timespec="seconds"),
        "git_commit": commit,
        "runs": [
            {
                "summary": {
                    split: {
                        "hit_at_1": r.for_split(split).hit_at_1,
                        "recall_at_3": r.for_split(split).recall_at_3,
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
    path = RESULTS_DIR / f"{now:%Y-%m-%d_%H%M}_retrieval.json"
    path.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n")
    return path


if __name__ == "__main__":
    sys.exit(main())
