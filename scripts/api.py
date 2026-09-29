"""Run the web app's API on this machine.

    docker compose up -d db
    uv run python scripts/db.py invite you@example.com --owner   # once
    uv run python scripts/api.py                                  # port 8001
    uv run python scripts/api.py --link you@example.com           # a fresh login link

Open the login link it (or `db.py invite`) printed, press "Sign in", and the
browser holds a 30-day session and opens JobLens at http://127.0.0.1:8001/
(7.7). /api/docs is an interactive page of every route underneath it: click
"Authorize" and enter 1 there first, which sends the X-JobLens header every
changing request needs.

Needs DATABASE_URL and the CV_* and EMBED_* settings (.env.example). The
database is migrated on start (safe to repeat), and the open, extracted
vacancies are loaded once: restart it after the nightly fetch. It binds to
127.0.0.1, over plain http, so its cookies are not marked Secure; hosting (7.8)
serves it over https, where they are.
"""

import argparse
import logging
import os
import sys
from pathlib import Path

import uvicorn
from dotenv import load_dotenv

from joblens.api import AppConfig, create_app
from joblens.config import load_llm_settings
from joblens.corpus import NAMES, load_corpus
from joblens.embeddings.store import SQLiteVectors, cache_path
from joblens.service import Models
from joblens.service.budget import Budgets
from joblens.storage import Database, published
from joblens.vault import Vault

ROOT = Path(__file__).parent.parent


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--port", type=int, default=8001)
    parser.add_argument(
        "--corpus",
        choices=(*NAMES, "db"),
        default="raw",
        help="db: the vacancies published into the database (publish_corpus.py)",
    )
    parser.add_argument("--link", metavar="EMAIL", help="print a login link for them")
    args = parser.parse_args()
    # A line at a time, even into a file or a process manager: the login link
    # printed below is no use sitting in a buffer until the server stops.
    sys.stdout.reconfigure(line_buffering=True)

    load_dotenv()
    logging.basicConfig(
        level=logging.INFO, format="%(levelname)s %(name)s: %(message)s"
    )
    try:
        database = Database.from_env(pool_size=5)
    except ValueError as err:
        print(err)
        return 1
    for one in database.migrate():
        print(f"applied {one.name}")

    base = f"http://127.0.0.1:{args.port}"
    if args.link:
        user = database.user_by_email(args.link)
        if user is None:
            print(f"No account for {args.link}: scripts/db.py invite {args.link}")
            return 1
        token = database.create_login_link(user.id)
        print(f"login link for {user.email}:\n  {base}/login#{token}")

    models = Models(
        cv=load_llm_settings(prefix="CV"), embed=load_llm_settings(prefix="EMBED")
    )
    cache_dir = ROOT / "data" / "cache"
    vectors = None
    if args.corpus == "db":
        # What publish_corpus.py put in the database (7.8.1): the same open,
        # extracted vacancies, with their vectors; the CV's own vectors go to
        # the local cache only (storage/published.py says why).
        corpus = published.load(database)
        if corpus is None:
            print("Nothing is published in this database: run publish_corpus.py")
            return 1
        model = published.published_model(database)
        if model != models.embed.model:
            print(
                f"The published vectors are {model}'s and EMBED_MODEL is "
                f"{models.embed.model}: one vector space only. Publish again."
            )
            return 1
        shared = published.published_vectors(database, model)

        def vectors(model: str) -> published.LayeredVectors:
            return published.LayeredVectors(
                shared, SQLiteVectors(cache_path(cache_dir, model))
            )

    else:
        # Open vacancies only, as match_cv.py: a job taken down is not a match.
        corpus = load_corpus(args.corpus, open_only=True).extracted()
    app = create_app(
        AppConfig(
            database=database,
            corpus=corpus,
            models=models,
            vectors=vectors,
            cache_dir=cache_dir,
            secure_cookies=False,  # plain http on this machine only
            vault=Vault.from_env(),  # None: own keys off (JOBLENS_SECRET_KEY)
            budgets=Budgets.from_env(),
            allow_local_providers=True,  # this machine's Ollama is the user's
            operator=os.environ.get("JOBLENS_OPERATOR") or None,
            contact=os.environ.get("JOBLENS_CONTACT") or None,
        )
    )
    print(
        f"{len(corpus)} open vacancies\nJobLens: {base}/   (API docs: {base}/api/docs)"
    )
    uvicorn.run(app, host="127.0.0.1", port=args.port, log_level="info")
    database.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
