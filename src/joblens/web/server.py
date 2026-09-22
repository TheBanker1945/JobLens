"""A local web server for the viewer, by hand, out of the standard library.

CLAUDE.md's rule is no framework until the thing has been built once, and this is
that once. What a framework would have given us is routing, JSON serialisation
and static files; all three are below, and together they are about a hundred
lines. Writing them once is also how you learn what FastAPI is doing for you when
we do reach for it.

**What is deliberately not here.** No authentication, because there is one user
and a fake login is worse than an honest none. No CORS, because the page is
served from the same origin as the API. No HTTPS. The server binds to 127.0.0.1
and refuses to be told otherwise by accident -- this reads a real CV and real
vacancies, and it is not something to put on a network.

**What is here because hosting is coming.** Every route is a function of a
request and returns data (`web/api.py` does the actual work), the routing table
is a list rather than a chain of ifs, and nothing in here knows what a file is
except the static handler. Swapping this for ASGI is replacing `_Handler` and
keeping everything else.
"""

import json
import mimetypes
import re
import traceback
from collections.abc import Callable
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

from joblens.corpus import Corpus
from joblens.storage import Store
from joblens.web import api

# Only these are ever served off disk, whatever is asked for.
SERVABLE = {".html", ".css", ".js", ".svg", ".ico", ".woff2"}


class Request:
    """A parsed request, so a route never touches the raw HTTP machinery."""

    def __init__(self, path: str, query: dict[str, list[str]], body: bytes = b""):
        self.path = path
        self.query = query
        self.body = body

    def get(self, name: str, default: str = "") -> str:
        return self.query.get(name, [default])[0]

    def integer(self, name: str, default: int) -> int:
        try:
            return int(self.get(name, str(default)))
        except ValueError as err:
            raise ValueError(f"{name} must be a number") from err

    def json(self) -> dict:
        if not self.body:
            raise ValueError("this request needs a JSON body")
        try:
            payload = json.loads(self.body)
        except json.JSONDecodeError as err:
            raise ValueError(f"the body is not JSON: {err}") from err
        if not isinstance(payload, dict):
            raise ValueError("the body must be a JSON object")
        return payload


Route = tuple[str, re.Pattern[str], Callable[..., dict]]


class Viewer:
    """The application: a store, a corpus, and the routes over them.

    Holds the corpus in memory because it is read on every page and re-reading
    283 files per request would make the viewer feel like the thing it is meant
    to replace. `--corpus` is chosen once, when the server starts.
    """

    def __init__(
        self, store: Store, corpus: Corpus, web_root: Path, judged_by: str = ""
    ):
        self.store = store
        self.corpus = corpus
        self.web_root = web_root
        # Whose opinion anything written from this page is. A labels file that
        # cannot say whose judgement it holds is worth nothing as evidence, so
        # the name comes from the command line and never from the browser.
        self.judged_by = judged_by
        labels = re.compile(r"^/api/runs/(?P<run_id>[^/]+)/labels$")
        self.routes: list[Route] = [
            ("GET", re.compile(r"^/api/runs$"), self.runs),
            ("GET", re.compile(r"^/api/runs/(?P<run_id>[^/]+)$"), self.run),
            ("GET", labels, self.labels),
            ("POST", labels, self.label),
            ("GET", re.compile(r"^/api/vacancy$"), self.vacancy),
            ("GET", re.compile(r"^/api/corpus$"), self.browse),
        ]

    # -- routes -------------------------------------------------------------

    def runs(self, request: Request) -> dict:
        return api.runs_view(self.store)

    def run(self, request: Request, run_id: str) -> dict:
        return api.run_view(self.store, self.corpus, unquote(run_id))

    def labels(self, request: Request, run_id: str) -> dict:
        return api.labels_view(self.store, unquote(run_id))

    def label(self, request: Request, run_id: str) -> dict:
        body = request.json() | {"judged_by": self.judged_by}
        return api.record_decision(self.store, self.corpus, unquote(run_id), body)

    def vacancy(self, request: Request) -> dict:
        key = request.get("key")
        if not key:
            raise ValueError("which vacancy? pass ?key=source:id")
        return api.vacancy_view(
            self.store, self.corpus, key, request.get("run") or None
        )

    def browse(self, request: Request) -> dict:
        return api.corpus_view(
            self.corpus, request.get("q"), limit=request.integer("limit", 100)
        )

    # -- the two things a route can be -------------------------------------

    def handle(self, method: str, path: str, query: dict, body: bytes) -> dict:
        """Find the route or raise. KeyError is a 404, ValueError a 400.

        Every route whose pattern matches is considered before giving up on the
        method: one path answers both a GET and a POST (labels), and a table
        that stopped at the first pattern would refuse the second one.
        """
        matched = False
        for route_method, pattern, handler in self.routes:
            found = pattern.match(path)
            if not found:
                continue
            matched = True
            if route_method == method:
                return handler(Request(path, query, body), **found.groupdict())
        if matched:
            raise PermissionError(f"{path} does not answer {method}")
        raise KeyError(f"no route for {path}")

    def static(self, path: str) -> tuple[bytes, str]:
        """A file from web/, and nothing else that happens to be on the disk."""
        relative = "index.html" if path == "/" else path.lstrip("/")
        target = (self.web_root / relative).resolve()
        if self.web_root.resolve() not in target.parents:
            raise KeyError(path)  # ../../.env, and the answer is no
        if target.suffix not in SERVABLE or not target.is_file():
            raise KeyError(path)
        kind = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
        return target.read_bytes(), kind


def build_handler(viewer: Viewer) -> type[BaseHTTPRequestHandler]:
    class _Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"
        server_version = "JobLens"

        def do_GET(self) -> None:  # noqa: N802 - the stdlib names it
            self._serve("GET")

        def do_POST(self) -> None:  # noqa: N802
            length = int(self.headers.get("Content-Length") or 0)
            self._serve("POST", self.rfile.read(length) if length else b"")

        def _serve(self, method: str, body: bytes = b"") -> None:
            parsed = urlparse(self.path)
            path = unquote(parsed.path)
            if not path.startswith("/api/"):
                return self._static(path)
            try:
                payload = viewer.handle(method, path, parse_qs(parsed.query), body)
            except KeyError as err:
                return self._json({"error": str(err)}, 404)
            except PermissionError as err:
                return self._json({"error": str(err)}, 405)
            except ValueError as err:
                return self._json({"error": str(err)}, 400)
            except Exception as err:  # noqa: BLE001 - a local tool says what broke
                traceback.print_exc()
                return self._json({"error": f"{type(err).__name__}: {err}"}, 500)
            self._json(payload, 200)

        def _static(self, path: str) -> None:
            try:
                content, kind = viewer.static(path)
            except KeyError:
                return self._json({"error": f"no {path}"}, 404)
            self._send(content, kind, 200)

        def _json(self, payload: dict, status: int) -> None:
            body = json.dumps(payload, ensure_ascii=False).encode()
            self._send(body, "application/json; charset=utf-8", status)

        def _send(self, body: bytes, kind: str, status: int) -> None:
            self.send_response(status)
            self.send_header("Content-Type", kind)
            self.send_header("Content-Length", str(len(body)))
            # A viewer is read while a run is being written; a cached page would
            # show yesterday's answer with today's confidence.
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, fmt: str, *args) -> None:
            # One line per request, without the stdlib's client-address prefix.
            print(f"  {fmt % args}")

    return _Handler


def serve(viewer: Viewer, port: int = 8000) -> ThreadingHTTPServer:
    """Bound to localhost only: this serves a real CV and real vacancies."""
    return ThreadingHTTPServer(("127.0.0.1", port), build_handler(viewer))
