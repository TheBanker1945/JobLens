"""Publish the open vacancies and their vectors into a database (7.8.1).

    uv run python scripts/publish_corpus.py                        # into DATABASE_URL
    uv run python scripts/publish_corpus.py --to NEON_DATABASE_URL # e.g. Neon

A hosted server has no data/raw, so it reads the vacancies it ranks from the
database (`api.py --corpus db`). This copies exactly what a server here would
load -- open, extracted, deduplicated (load_corpus("raw", open_only=True)) --
with one vector per vacancy from the local embedding cache, in one
transaction that replaces what was published before. A vacancy without a
vector yet is embedded first with the EMBED_* model (index_vacancies.py
normally did that already). Run it after the nightly fetch and index; a
server picks the new set up on its next start.

`--to` names the environment variable that holds the address, so the address
itself is never typed on a command line or printed.
"""

import argparse
import os
import sys
import time
from pathlib import Path

from dotenv import load_dotenv

from joblens.config import load_llm_settings
from joblens.corpus import load_corpus
from joblens.embeddings.client import EmbeddingClient
from joblens.embeddings.store import CachedEmbedder, cache_path
from joblens.storage import Database
from joblens.storage.published import document_vectors, publish

ROOT = Path(__file__).parent.parent


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--to",
        default="DATABASE_URL",
        metavar="ENV_NAME",
        help="the environment variable holding the database address",
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="count what would be published"
    )
    args = parser.parse_args()
    load_dotenv()
    url = os.environ.get(args.to)
    if not url:
        print(f"{args.to} is not set (in .env or the environment).")
        return 1

    started = time.monotonic()
    corpus = load_corpus("raw", open_only=True).extracted()
    settings = load_llm_settings(prefix="EMBED")
    with EmbeddingClient(settings) as client:
        cached = CachedEmbedder(
            client, cache_path(ROOT / "data" / "cache", settings.model)
        )
        vectors = document_vectors(corpus, cached)
        cached.close()
    print(corpus.funnel.line())
    print(
        f"{len(corpus)} vacancies, {len(vectors)} vectors ({settings.model}); "
        f"{cached.misses} embedded now, {cached.hits} from the local cache"
    )
    if args.dry_run:
        return 0

    database = Database(url)
    for one in database.migrate():
        print(f"applied {one.name}")
    done = publish(database, corpus, vectors, settings.model)
    database.close()
    print(
        f"published into {args.to} at {done.at:%Y-%m-%d %H:%M} "
        f"in {time.monotonic() - started:.0f}s"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
