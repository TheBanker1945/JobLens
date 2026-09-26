"""The viewer: a local page over the runs that have been stored.

    uv run python scripts/serve.py
    uv run python scripts/serve.py --port 8080 --corpus samples

It shows what a run recommended, what it read and turned down, and
what it never showed anybody -- the third of those being the rejection that was
invisible until 4.1 stored the whole ranking. The vacancy text and the extracted
fields are one click away, because a rejection is checked against the advert and
not against a summary of it.

Marking a vacancy writes to the labels the evals already read, and a mark needs a
one-line reason -- a disagreement without one is what made the first set of
labels unusable. Pass --judged-by so the file can say whose opinion it holds.

Nothing here judges or fetches anything: matching is still scripts/match_cv.py,
and scraping still happens on a schedule. No model is ever called from a page.
"""

import argparse
import sys
import webbrowser
from pathlib import Path

from dotenv import load_dotenv

from joblens.corpus import NAMES, load_corpus
from joblens.storage import Database, FileStore
from joblens.web.server import Viewer, serve

ROOT = Path(__file__).parent.parent
WEB = ROOT / "web"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--corpus", choices=NAMES, default="raw")
    parser.add_argument("--open", action="store_true", help="open a browser too")
    parser.add_argument(
        "--judged-by",
        default="",
        help="your name, if you are going to mark vacancies. A labels file that "
        "cannot say whose judgement it holds is worth nothing as evidence",
    )
    parser.add_argument(
        "--db",
        metavar="EMAIL",
        help="read the runs and labels of this account in the database "
        "(scripts/db.py) instead of the files in data/raw/",
    )
    args = parser.parse_args()

    corpus = load_corpus(args.corpus)
    store = FileStore(ROOT) if not args.db else account_store(args.db)
    if store is None:
        return 1
    runs = store.runs()
    print(f"{len(corpus)} vacancies in the {args.corpus} corpus, {len(runs)} run(s)")
    if not runs:
        print("No runs stored yet. scripts/match_cv.py writes one each time it judges.")

    server = serve(Viewer(store, corpus, WEB, args.judged_by), args.port)
    address = f"http://127.0.0.1:{args.port}/"
    print(f"serving {address}  (ctrl-c to stop)")
    if args.open:
        webbrowser.open(address)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped.")
    finally:
        server.server_close()
    return 0


def account_store(email: str):
    """One account's store: the viewer is the same page over either store."""
    load_dotenv()
    database = Database.from_env()
    user = database.user_by_email(email)
    if user is None:
        print(f"No account for {email}. scripts/db.py create-user makes one.")
        return None
    return database.store_for(user.id)


if __name__ == "__main__":
    sys.exit(main())
