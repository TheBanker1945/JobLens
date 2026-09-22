"""The viewer: a local page over the runs that have been stored.

    uv run python scripts/serve.py
    uv run python scripts/serve.py --port 8080 --corpus samples

Read-only. It shows what a run recommended, what it read and turned down, and
what it never showed anybody -- the third of those being the rejection that was
invisible until 4.1 stored the whole ranking. The vacancy text and the extracted
fields are one click away, because a rejection is checked against the advert and
not against a summary of it.

Nothing here judges or fetches anything: matching is still scripts/match_cv.py,
and scraping still happens on a schedule. This reads what is already on disk.
"""

import argparse
import sys
import webbrowser
from pathlib import Path

from joblens.corpus import NAMES, load_corpus
from joblens.storage import FileStore
from joblens.web.server import Viewer, serve

ROOT = Path(__file__).parent.parent
WEB = ROOT / "web"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--corpus", choices=NAMES, default="raw")
    parser.add_argument("--open", action="store_true", help="open a browser too")
    args = parser.parse_args()

    corpus = load_corpus(args.corpus)
    store = FileStore(ROOT)
    runs = store.runs()
    print(f"{len(corpus)} vacancies in the {args.corpus} corpus, {len(runs)} run(s)")
    if not runs:
        print("No runs stored yet. scripts/match_cv.py writes one each time it judges.")

    server = serve(Viewer(store, corpus, WEB), args.port)
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


if __name__ == "__main__":
    sys.exit(main())
