"""The web app's API: what a browser page (7.7) will call, as JSON (7.4).

**FastAPI, after building it by hand once.** 4.3 wrote routing, JSON and error
codes out of the standard library (joblens/web/server.py), which was CLAUDE.md's
"build it once". What FastAPI adds on top of that is what a public app needs and
the viewer did not: request bodies checked against the pydantic models the rest
of JobLens already uses (a bad preferences form is refused with the field that
is wrong, before any code of ours runs), file uploads, a `Depends` that says
who is asking (7.5 swaps it for login without touching a route), and an
interactive page of every route, generated from this file: /api/docs.

**Every route is a few lines around a service call.** The work is in
joblens.service (upload, match) and joblens.storage (everything kept); this
file turns HTTP into calls and ServiceErrors into status codes. Handlers are
plain `def`, not `async def`: they wait on Postgres and on model providers with
blocking clients, and FastAPI runs a plain function in a thread for exactly
that.

**Until 7.5 there is no login.** The "user" is the one account named in
JOBLENS_DEV_USER, and three rules keep that safe on a laptop:

- it binds to 127.0.0.1 (scripts/api.py), and a Host header naming anything
  else is refused, so a domain pointed at 127.0.0.1 cannot read it (DNS
  rebinding, the viewer's rule);
- **every request that changes something must carry `X-JobLens: 1`.** Another
  web page open in the same browser can make it send a form, even an upload, to
  127.0.0.1, but it cannot add a header of its own without asking this server
  first (a CORS preflight), and this server never says yes. So another site
  cannot upload a CV into your account or start a paid match;
- nothing here returns a key or a secret; the model settings stay in .env.
"""

import logging
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Annotated

from fastapi import Depends, FastAPI, Form, HTTPException, Request, UploadFile
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.responses import JSONResponse
from fastapi.security import APIKeyHeader
from pydantic import BaseModel, Field

from joblens.api.runner import Runner, ThreadRunner
from joblens.corpus import Corpus
from joblens.cv.read import CVFile
from joblens.cv.schema import CVProfile
from joblens.embeddings.client import EmbeddingClient
from joblens.llm.client import LLMClient
from joblens.preferences import Preferences
from joblens.service import (
    MAX_UPLOAD_BYTES,
    CVUnreadable,
    Models,
    ProviderRefused,
    ServiceError,
    add_cv,
    run_match_job,
)
from joblens.service.matching import ChatFactory, EmbedFactory
from joblens.storage import CVRecord, Database, Job, PostgresStore, RunSummary, User
from joblens.web import api as viewer

logger = logging.getLogger(__name__)

# The header every changing request must carry (module docstring).
HEADER = "X-JobLens"
OUR_PAGE = APIKeyHeader(
    name=HEADER,
    auto_error=False,
    description="Set to 1. Every request that changes something needs it.",
)
SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}


@dataclass
class AppConfig:
    """Everything the app needs, handed in: scripts/api.py builds it from .env,
    a test builds it from fakes."""

    database: Database
    corpus: Corpus  # open, extracted vacancies, loaded once at start
    models: Models  # the operator's models; 7.6 lets a user bring their own
    cache_dir: Path
    dev_user: str | None = None  # the e-mail of the one account, until 7.5
    chat: ChatFactory = LLMClient
    embed: EmbedFactory = EmbeddingClient
    runner: Runner = field(default_factory=ThreadRunner)
    allowed_hosts: tuple[str, ...] = ("127.0.0.1", "localhost")


class CVSummary(BaseModel):
    """A CV in a list: enough to choose one, without its text."""

    id: str
    name: str
    filename: str
    uploaded_at: datetime
    active: bool
    removed: dict[str, int]  # what redaction took out, counted
    characters: int
    has_profile: bool

    @classmethod
    def of(cls, cv: CVRecord) -> "CVSummary":
        return cls(
            **cv.model_dump(include=set(cls.model_fields) & set(CVRecord.model_fields)),
            characters=len(cv.text),
            has_profile=cv.profile is not None,
        )


class CVDetail(CVSummary):
    """One CV, with exactly the text that is sent to a model."""

    text: str
    profile: CVProfile | None

    @classmethod
    def of(cls, cv: CVRecord) -> "CVDetail":
        return cls(**CVSummary.of(cv).model_dump(), text=cv.text, profile=cv.profile)


class MatchAsk(BaseModel):
    top: int = Field(10, ge=1, le=20, description="how many vacancies to judge")


def create_app(config: AppConfig) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        interrupted = config.database.fail_interrupted_jobs()
        if interrupted:
            logger.warning("%d job(s) were cut off by a restart", interrupted)
        yield
        config.runner.shutdown()

    app = FastAPI(
        title="JobLens",
        summary="Match a CV and what someone wants against Dutch vacancies.",
        version="7.4",
        lifespan=lifespan,
        docs_url="/api/docs",
        redoc_url=None,
        openapi_url="/api/openapi.json",
        # Declared so /api/docs shows an "Authorize" button: type 1 there once
        # and the page sends the header with every request. It checks nothing
        # itself (auto_error=False); the middleware below does.
        dependencies=[Depends(OUR_PAGE)],
    )
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=list(config.allowed_hosts))

    @app.middleware("http")
    async def asked_by_our_own_page(request: Request, call_next):
        if request.method not in SAFE_METHODS and request.headers.get(HEADER) != "1":
            return JSONResponse(
                {
                    "detail": f"Send this with the header {HEADER}: 1. Requests "
                    "that change something are only taken from JobLens's own page."
                },
                status_code=403,
            )
        return await call_next(request)

    @app.exception_handler(ServiceError)
    async def service_error(request: Request, err: ServiceError) -> JSONResponse:
        """Whose fault it is decides the code: the CV's (422), a provider that
        is busy (503, try later), or one that refused or cannot be reached."""
        if isinstance(err, CVUnreadable):
            return JSONResponse({"detail": str(err)}, status_code=422)
        busy = isinstance(err, ProviderRefused) and err.busy
        return JSONResponse(
            {"detail": str(err)},
            status_code=503 if busy else 502,
            headers={"Retry-After": "120"} if busy else None,
        )

    # -- who is asking ------------------------------------------------------

    def current_user() -> User:
        """The account a request acts for. Until 7.5: the configured one."""
        if not config.dev_user:
            raise HTTPException(
                401, "Nobody is signed in. Set JOBLENS_DEV_USER to an account."
            )
        user = config.database.user_by_email(config.dev_user)
        if user is None:
            raise HTTPException(
                401, f"No account for {config.dev_user}: scripts/db.py create-user"
            )
        return user

    def current_store(user: Annotated[User, Depends(current_user)]) -> PostgresStore:
        return config.database.store_for(user.id)

    Me = Annotated[User, Depends(current_user)]
    Mine = Annotated[PostgresStore, Depends(current_store)]

    # -- routes -------------------------------------------------------------

    @app.get("/api/health")
    def health() -> dict:
        return {"ok": True, "vacancies": len(config.corpus)}

    @app.get("/api/me")
    def me(user: Me) -> User:
        return user

    @app.get("/api/cvs")
    def cvs(store: Mine) -> list[CVSummary]:
        return [CVSummary.of(one) for one in store.cvs()]

    @app.get("/api/cvs/active")
    def active_cv(store: Mine) -> CVDetail:
        cv = store.active_cv()
        if cv is None:
            raise HTTPException(404, "No CV yet: upload one.")
        return CVDetail.of(cv)

    @app.post("/api/cvs", status_code=201)
    def upload_cv(
        store: Mine,
        file: UploadFile,
        strip_name: Annotated[str | None, Form()] = None,
    ) -> CVDetail:
        """Read, redact and profile a CV (about half a cent), and make it the
        active one. `strip_name` is your name exactly as the CV writes it."""
        # One byte over the limit is enough to know it is too big, without
        # reading a whole oversized file into memory.
        data = file.file.read(MAX_UPLOAD_BYTES + 1)
        record = add_cv(
            store,
            CVFile(file.filename or "cv", data),
            config.models,
            cache_dir=config.cache_dir,
            strip_name=(strip_name or "").strip() or None,
            chat=config.chat,
        )
        return CVDetail.of(record)

    @app.post("/api/cvs/{cv_id}/activate")
    def activate_cv(store: Mine, cv_id: str) -> CVDetail:
        try:
            return CVDetail.of(store.activate_cv(cv_id))
        except KeyError as err:
            raise HTTPException(404, "No such CV.") from err

    @app.get("/api/preferences")
    def preferences(store: Mine) -> Preferences:
        return Preferences.model_validate(store.load_preferences() or {})

    @app.put("/api/preferences")
    def save_preferences(store: Mine, answers: Preferences) -> Preferences:
        """Replace all answers. A blank field is no preference."""
        store.save_preferences(answers.model_dump(mode="json"))
        return answers

    @app.post("/api/matches", status_code=202)
    def start_match(store: Mine, ask: MatchAsk | None = None) -> Job:
        """Start matching the active CV with the saved preferences. Returns at
        once; follow the job at /api/matches/{id}. Costs about 4 cents."""
        if store.active_cv() is None:
            raise HTTPException(409, "Upload a CV first: there is nothing to match.")
        try:
            job = store.start_job("match", (ask or MatchAsk()).model_dump())
        except ValueError as err:
            raise HTTPException(409, str(err)) from err
        config.runner.submit(
            run_match_job,
            config.database,
            str(store.user_id),
            job.id,
            corpus=config.corpus,
            models=config.models,
            cache_dir=config.cache_dir,
            chat=config.chat,
            embed=config.embed,
        )
        return store.job(job.id)

    @app.get("/api/matches")
    def matches(store: Mine) -> list[Job]:
        return store.jobs()

    @app.get("/api/matches/{job_id}")
    def match(store: Mine, job_id: str) -> Job:
        try:
            return store.job(job_id)
        except KeyError as err:
            raise HTTPException(404, "No such match.") from err

    @app.get("/api/runs")
    def runs(store: Mine) -> list[RunSummary]:
        return store.runs()

    @app.get("/api/runs/{run_id}")
    def run(store: Mine, run_id: str) -> dict:
        """A stored run: what it recommended, turned down, and never read."""
        try:
            return viewer.run_view(store, config.corpus, run_id)
        except KeyError as err:
            raise HTTPException(404, "No such run.") from err

    return app
