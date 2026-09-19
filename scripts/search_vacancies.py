"""Semantic search over the sample vacancies: rank them by meaning, not keywords.

Usage:
    uv run python scripts/search_vacancies.py "python baan in amsterdam"
    uv run python scripts/search_vacancies.py "werken met je handen" --top 3
    uv run python scripts/search_vacancies.py "python baan" --no-instruction
"""

import argparse
import sys
import time
from pathlib import Path

import httpx
import openai

from joblens.config import load_llm_settings
from joblens.embeddings.client import EmbeddingClient
from joblens.embeddings.similarity import rank

SAMPLES = Path(__file__).parent.parent / "data" / "samples" / "vacancies"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("query")
    parser.add_argument("--top", type=int, default=10)
    parser.add_argument(
        "--no-instruction",
        action="store_true",
        help="embed the query as plain text, to see what the instruction changes",
    )
    args = parser.parse_args()

    settings = load_llm_settings(prefix="EMBED")
    paths = sorted(SAMPLES.glob("*.txt"))
    texts = [p.read_text(encoding="utf-8") for p in paths]

    try:
        with EmbeddingClient(settings) as client:
            start = time.perf_counter()
            doc_vectors = client.embed_documents(texts)
            took = time.perf_counter() - start
            if args.no_instruction:
                query_vector = client.embed_documents([args.query])[0]
            else:
                query_vector = client.embed_query(args.query)
    except (httpx.ConnectError, openai.APIConnectionError):
        print(f"Cannot reach {settings.base_url}. Is the embedding server running?")
        return 1

    print(
        f"model: {settings.model}  |  {len(texts)} vacancies -> "
        f"{len(doc_vectors[0])} numbers each, in {took:.1f}s"
    )
    print(
        f"query: {args.query!r}{'  (no instruction)' if args.no_instruction else ''}\n"
    )
    for position, hit in enumerate(rank(query_vector, doc_vectors, args.top), 1):
        title = texts[hit.index].splitlines()[0]
        print(f"{position:>2}. {hit.score:.3f}  {paths[hit.index].stem:<32} {title}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
