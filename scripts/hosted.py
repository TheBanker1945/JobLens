"""The web app as it runs hosted (7.8.2): every setting from the environment.

    docker build -t joblens .
    docker run -p 8080:8080 --env-file hosted.env joblens

Cloud Run starts this inside the container (Dockerfile). Unlike api.py it
reads no .env file -- a container gets its settings, and its secrets, from its
environment -- it answers on 0.0.0.0:$PORT, it reads the vacancies it ranks
from the database (publish_corpus.py puts them there), and it turns off what
only makes sense on your own machine: cookies over plain http, and model
providers on localhost.

Settings (docs/hosting-phase-7.8.md):
  DATABASE_URL        the database, e.g. Neon's direct address (a secret)
  JOBLENS_HOSTS       the host names it answers to, comma-separated, e.g.
                      joblens-abc123-ez.a.run.app; anything else is refused
  CV_*, EMBED_*, GEMINI_API_KEY   the models, as in .env.example
  JOBLENS_SECRET_KEY  encrypts own API keys; without it own keys are off
  JOBLENS_TESTER_MONTHLY_USD, JOBLENS_OPERATOR_MONTHLY_USD   (optional)
  JOBLENS_OPERATOR, JOBLENS_CONTACT   who runs it, for the privacy page
  PORT                set by Cloud Run; 8080 otherwise

Cookies are Secure unless every host in JOBLENS_HOSTS is this machine, which
is create_app's own rule: a container tried out on 127.0.0.1 works over plain
http, and one on a real address cannot.
"""

import logging
import os
import sys
import tempfile
from datetime import timedelta
from pathlib import Path

import uvicorn

from joblens.api import AppConfig, create_app
from joblens.config import load_llm_settings
from joblens.embeddings.store import SQLiteVectors, cache_path
from joblens.service import Models
from joblens.service.budget import Budgets
from joblens.storage import Database, published
from joblens.vault import Vault

LOCAL = {"127.0.0.1", "localhost"}


def main() -> int:
    sys.stdout.reconfigure(line_buffering=True)
    logging.basicConfig(
        level=logging.INFO, format="%(levelname)s %(name)s: %(message)s"
    )
    hosts = tuple(
        one.strip()
        for one in os.environ.get("JOBLENS_HOSTS", "").split(",")
        if one.strip()
    )
    if not hosts:
        print("JOBLENS_HOSTS is not set: the host names this server answers to.")
        return 1
    try:
        database = Database.from_env(pool_size=5)
    except ValueError as err:
        print(err)
        return 1
    for one in database.migrate():
        print(f"applied {one.name}")

    models = Models(
        cv=load_llm_settings(prefix="CV"), embed=load_llm_settings(prefix="EMBED")
    )
    corpus = published.load(database)
    if corpus is None:
        print("No vacancies are published in this database: run publish_corpus.py.")
        return 1
    model = published.published_model(database)
    if model != models.embed.model:
        print(f"Published vectors are {model}'s, EMBED_MODEL is {models.embed.model}.")
        return 1
    shared = published.published_vectors(database, model)
    # This container's own disk, gone when it stops: the CV side of the
    # vectors and the model caches, never the shared tables (storage/published.py).
    cache_dir = Path(tempfile.mkdtemp(prefix="joblens-"))

    def vectors(model: str) -> published.LayeredVectors:
        return published.LayeredVectors(
            shared, SQLiteVectors(cache_path(cache_dir, model))
        )

    app = create_app(
        AppConfig(
            database=database,
            corpus=corpus,
            models=models,
            vectors=vectors,
            cache_dir=cache_dir,
            allowed_hosts=hosts,
            secure_cookies=not set(hosts) <= LOCAL,
            vault=Vault.from_env(),  # None: own keys off, and the page says so
            budgets=Budgets.from_env(),
            allow_local_providers=False,  # "localhost" is this server, not them
            # What the privacy page promises happens by itself (7.8.3): old
            # upload files, links and sessions go at start and every 6 hours.
            purge_every=timedelta(hours=6),
            operator=os.environ.get("JOBLENS_OPERATOR") or None,
            contact=os.environ.get("JOBLENS_CONTACT") or None,
        )
    )
    if not (os.environ.get("JOBLENS_OPERATOR") and os.environ.get("JOBLENS_CONTACT")):
        print(
            "WARNING: the privacy page names nobody. Set JOBLENS_OPERATOR and "
            "JOBLENS_CONTACT before inviting testers."
        )
    port = int(os.environ.get("PORT", "8080"))
    print(f"{len(corpus)} vacancies ({model}); answering {', '.join(hosts)} on :{port}")
    # No access log of our own: Cloud Run keeps one already, and a second
    # would copy every visitor's address into another place.
    uvicorn.run(
        app,
        host="0.0.0.0",
        port=port,
        access_log=False,
        proxy_headers=True,
        forwarded_allow_ips="*",
    )
    database.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
