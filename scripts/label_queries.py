"""Label which vacancies are a correct answer to a query. A human sits here.

The eval can only measure what someone has judged, and for the real corpus that
someone is you. This shows candidate vacancies per query and records your
decisions in evals/queries/<corpus>.json, which the eval then reads.

Candidates are **pooled**: the union of the top results of every variant in
evals/retrieval.toml, not of one of them. Judging only what the current default
finds would quietly rig the comparison -- a variant that surfaces a good vacancy
nobody was asked about would be marked wrong for finding it.

Both answers are kept. `relevant` is what counts as correct; `judged` is every
vacancy you were shown, so a later pooling round (a new model, chunking) only
asks about candidates that are genuinely new.

Usage:
    uv run python scripts/label_queries.py                      # corpus raw
    uv run python scripts/label_queries.py --only verpleegkundige
    uv run python scripts/label_queries.py --pool-depth 5       # fewer candidates

Stop whenever you like with q: every answer so far is already saved.
"""

import argparse
import json
import sys
from pathlib import Path

import httpx
import openai
from dotenv import load_dotenv

from joblens.corpus import NAMES, Corpus, load_corpus
from joblens.embeddings.documents import build_document
from joblens.evals.retrieval import (
    Query,
    RetrievalConfig,
    embed,
    load_configs,
    load_queries,
    merge_draft,
    rank_all,
)
from joblens.sources.base import Vacancy

ROOT = Path(__file__).parent.parent
QUERIES_DIR = ROOT / "evals" / "queries"
CACHE_DIR = ROOT / "data" / "cache"

MENU = "  relevant? [y]es  [n]o  [v]iew full text  [s]kip query  [q]uit and save"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--corpus", choices=NAMES, default="raw")
    parser.add_argument("--config", type=Path, default=ROOT / "evals/retrieval.toml")
    parser.add_argument("--pool-depth", type=int, default=10)
    parser.add_argument("--only", help="only queries containing this text")
    args = parser.parse_args()

    load_dotenv()
    draft_path = QUERIES_DIR / f"{args.corpus}.draft.json"
    out_path = QUERIES_DIR / f"{args.corpus}.json"
    if not draft_path.exists():
        print(f"No draft queries at {draft_path.relative_to(ROOT)}.")
        return 1

    draft = json.loads(draft_path.read_text(encoding="utf-8"))
    corpus = load_corpus(args.corpus).extracted()
    existing = load_queries(out_path) if out_path.exists() else []
    queries, dropped = merge_draft(draft, existing)
    for query in dropped:
        print(f"  dropped (no longer in the draft): {query!r}")
    todo = [q for q in queries if not args.only or args.only in q.query]
    print(f"corpus {corpus.name}: {len(corpus)} vacancies, {len(queries)} queries")

    configs = usable_configs(load_configs(args.config))
    if not configs:
        print("No usable variants in the config: nothing to pool candidates from.")
        return 1
    try:
        pool = build_pool(configs, corpus, todo, args.pool_depth)
    except (httpx.ConnectError, openai.APIConnectionError) as err:
        print(f"Cannot reach the embedding server: {err}")
        return 1

    label(todo, pool, corpus, queries, out_path)
    return 0


def usable_configs(configs: list[RetrievalConfig]) -> list[RetrievalConfig]:
    """One config per distinct way of embedding, skipping any with no API key.

    Two variants with the same model, style and instruction rank identically, so
    pooling both would only spend time re-reading the cache.
    """
    usable, seen = [], set()
    for config in configs:
        signature = (config.model, config.style, config.instruction)
        if signature in seen:
            continue
        try:
            config.settings()
        except ValueError as err:
            print(f"  skipping {config.name}: {err}")
            continue
        seen.add(signature)
        usable.append(config)
    return usable


def build_pool(
    configs: list[RetrievalConfig], corpus: Corpus, queries: list[Query], depth: int
) -> dict[str, list[str]]:
    """Per query, the vacancy keys any variant puts in its top `depth`.

    Ordered by the best rank a candidate reached in any variant, so the likeliest
    answers come first and a long tail of obvious misses comes last.
    """
    best: dict[str, dict[str, int]] = {q.query: {} for q in queries}
    for config in configs:
        print(f"  pooling from {config.name} ...", flush=True)
        documents = [
            build_document(v.text, corpus.details.get(v.key), config.style)
            for v in corpus.vacancies
        ]
        vectors = embed(config, documents, [q.query for q in queries], CACHE_DIR)
        rankings = rank_all(vectors.queries, vectors.documents)
        for query, ranking in zip(queries, rankings, strict=True):
            for position, index in enumerate(ranking[:depth], 1):
                key = corpus.vacancies[index].key
                ranks = best[query.query]
                ranks[key] = min(ranks.get(key, position), position)
    return {
        query: [key for key, _ in sorted(ranks.items(), key=lambda pair: pair[1])]
        for query, ranks in best.items()
    }


def label(
    todo: list[Query],
    pool: dict[str, list[str]],
    corpus: Corpus,
    all_queries: list[Query],
    out_path: Path,
) -> None:
    known = corpus.by_key()
    for number, query in enumerate(todo, 1):
        candidates = [key for key in pool[query.query] if key not in query.judged]
        header = f"\n===== [{number}/{len(todo)}] {query.query!r} ({query.split})"
        if not candidates:
            print(f"{header} -- already done, {len(query.relevant)} relevant")
            continue
        print(header)
        if query.note:
            print(f"  note: {query.note}")
        print(
            f"  {len(candidates)} new candidates, {len(query.relevant)} relevant so far"
        )

        for seen, key in enumerate(candidates, 1):
            answer = ask(known[key], seen, len(candidates))
            if answer == "q":
                save(all_queries, out_path)
                print(f"\nStopped. Saved to {out_path.relative_to(ROOT)}.")
                return
            if answer == "s":
                break
            query.judged.append(key)
            if answer == "y":
                query.relevant.append(key)
            save(all_queries, out_path)  # after every answer: nothing is ever lost

    save(all_queries, out_path)
    unanswerable = [q.query for q in all_queries if not q.relevant]
    print(f"\nDone. Saved to {out_path.relative_to(ROOT)}.")
    if unanswerable:
        print("Queries with no relevant vacancy (drop them, or the eval scores 0):")
        for query in unanswerable:
            print(f"  {query!r}")


def ask(vacancy: Vacancy, seen: int, total: int) -> str:
    print(f"\n  [{seen}/{total}] {describe(vacancy)}")
    print(f"      {snippet(vacancy.text)}")
    while True:
        print(MENU)
        answer = input("  > ").strip().lower()[:1]
        if answer in ("y", "n", "s", "q"):
            return answer
        if answer == "v":
            print(f"\n{vacancy.text}\n")
        else:
            print("  (y, n, v, s or q)")


def describe(vacancy: Vacancy) -> str:
    where = " · ".join(part for part in (vacancy.company, vacancy.city) if part)
    return f"{vacancy.title}  |  {where or 'unknown'}  ({vacancy.source})"


def snippet(text: str, width: int = 220) -> str:
    flat = " ".join(text.split())
    return flat[:width] + ("..." if len(flat) > width else "")


def save(queries: list[Query], out_path: Path) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    payload = [q.model_dump() for q in queries]
    out_path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )


if __name__ == "__main__":
    sys.exit(main())
