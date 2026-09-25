"""Run the web app's API on this machine.

    docker compose up -d db
    uv run python scripts/api.py                      # http://127.0.0.1:8001/api/docs
    uv run python scripts/api.py --port 8002 --user you@example.com

It serves JSON to the page that 7.7 builds; until then /api/docs is an
interactive page of every route, where a CV can be uploaded and a match started
by hand. Needs DATABASE_URL, the CV_* and EMBED_* settings (.env.example), and
an account: JOBLENS_DEV_USER in .env, or --user. There is no login until 7.5,
so it binds to 127.0.0.1 and nothing else.

The database is migrated on start (safe to repeat: scripts/db.py), and the
open, extracted vacancies are loaded once; restart it after the nightly fetch.
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
from joblens.service import Models
from joblens.storage import Database

ROOT = Path(__file__).parent.parent


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--port", type=int, default=8001)
    parser.add_argument("--corpus", choices=NAMES, default="raw")
    parser.add_argument("--user", help="the account to act as (JOBLENS_DEV_USER)")
    args = parser.parse_args()

    load_dotenv()
    logging.basicConfig(
        level=logging.INFO, format="%(levelname)s %(name)s: %(message)s"
    )
    try:
        database = Database.from_env(pool_size=5)
    except ValueError as err:
        print(err)
        return 1
    applied = database.migrate()
    for one in applied:
        print(f"applied {one.name}")

    # Open vacancies only, as match_cv.py: a job taken down is not a match.
    corpus = load_corpus(args.corpus, open_only=True).extracted()
    user = args.user or os.environ.get("JOBLENS_DEV_USER")
    if not user or database.user_by_email(user) is None:
        print(
            f"No account for {user!r}. Make one with scripts/db.py create-user and "
            "set JOBLENS_DEV_USER (or pass --user)."
        )
        return 1

    app = create_app(
        AppConfig(
            database=database,
            corpus=corpus,
            models=Models(
                cv=load_llm_settings(prefix="CV"),
                embed=load_llm_settings(prefix="EMBED"),
            ),
            cache_dir=ROOT / "data" / "cache",
            dev_user=user,
        )
    )
    print(
        f"{len(corpus)} open vacancies; acting as {user}\n"
        f"API docs: http://127.0.0.1:{args.port}/api/docs"
    )
    # 127.0.0.1 only: there is no login until 7.5.
    uvicorn.run(app, host="127.0.0.1", port=args.port, log_level="info")
    database.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
