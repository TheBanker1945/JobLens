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

**Signing in (7.5).** Invite-only: the owner makes an account and a one-time
login link (scripts/db.py invite), the link opens a page whose button starts a
session, and the session is an HttpOnly cookie that lasts 30 days. The
database keeps a hash of each link and session, never the token. Every route
but /api/health and the login itself answers 401 without a valid session, and
every query below runs through that person's store, so one person's ids open
nothing of another's.

Two rules from 7.4 stay, now as the cookie session's protection:

- a Host header naming a site this server does not serve is refused (DNS
  rebinding);
- **every request that changes something must carry `X-JobLens: 1`.** A page
  on another site can make your browser send a form -- with your cookie -- to
  this one, but it cannot add a header of its own without a CORS preflight,
  which this server never allows. That, with SameSite=Lax on the cookie, is the
  CSRF protection.
"""

import logging
from collections.abc import Callable
from contextlib import asynccontextmanager
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Literal

from fastapi import (
    Depends,
    FastAPI,
    Form,
    HTTPException,
    Request,
    Response,
    UploadFile,
)
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.security import APIKeyCookie, APIKeyHeader
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, SecretStr

from joblens.api import language, views
from joblens.api.runner import Runner, ThreadRunner
from joblens.config import LLMSettings
from joblens.corpus import Corpus
from joblens.cv.read import CVFile
from joblens.cv.runs import RunRecord
from joblens.embeddings.client import EmbeddingClient
from joblens.evals.matching import CVLabels
from joblens.llm.client import LLMClient
from joblens.llm.presets import MEASURED, available
from joblens.llm.pricing import cost_usd
from joblens.preferences import Preferences
from joblens.preferences.places import place_names
from joblens.service import (
    MAX_UPLOAD_BYTES,
    CVUnreadable,
    Models,
    ProviderRefused,
    ServiceError,
    add_cv,
    ai,
    budget,
    run_match_job,
)
from joblens.service.ai import OwnKeysOff, check_provider
from joblens.service.budget import Budgets, BudgetSpent
from joblens.service.matching import ChatFactory, EmbedFactory
from joblens.storage import Database, Job, PostgresStore, RunSummary, User
from joblens.storage.postgres import SESSION_VALID
from joblens.vault import Vault, VaultError
from joblens.web import api as viewer

logger = logging.getLogger(__name__)

# The header every changing request must carry (module docstring).
HEADER = "X-JobLens"
COOKIE = "joblens_session"
SESSION_COOKIE = APIKeyCookie(name=COOKIE, auto_error=False)
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
    chat: ChatFactory = LLMClient
    embed: EmbedFactory = EmbeddingClient
    runner: Runner = field(default_factory=ThreadRunner)
    allowed_hosts: tuple[str, ...] = ("127.0.0.1", "localhost")
    # Secure cookies travel over HTTPS only. Off is allowed on this machine
    # alone (plain http://127.0.0.1); create_app refuses it anywhere else.
    secure_cookies: bool = True
    after_sign_in: str = "/"  # the dashboard (7.7)
    # Own keys (7.6): encrypted with this; None switches them off.
    vault: Vault | None = None
    budgets: Budgets = field(default_factory=Budgets)
    allow_local_providers: bool = False  # Ollama/LM Studio: this machine only
    check_provider: Callable[[LLMSettings], None] = check_provider


class MatchAsk(BaseModel):
    top: int = Field(10, ge=1, le=20, description="how many vacancies to judge")


class BringKey(BaseModel):
    provider: str = Field(description="one of GET /api/ai/providers")
    model: str = Field(description="the exact model id, from the provider's list")
    api_key: SecretStr = Field(description="stored encrypted, never shown again")
    thinking: bool = False


class SignIn(BaseModel):
    token: str = Field(description="the code after # in a login link")


class AboutMe(BaseModel):
    """What a person may change about their account from the page."""

    locale: Literal["en", "nl", "de", "fr", "es"] | None = None
    display_name: str | None = Field(None, min_length=1, max_length=80)
    onboarded: Literal[True] | None = Field(
        None, description="the guide is done, or skipped: do not show it again"
    )


class Goodbye(BaseModel):
    confirm: str = Field(description='exactly "delete everything"')


class Everything(BaseModel):
    """All JobLens keeps about a person, for "download my data"."""

    exported_at: datetime
    user: User
    cvs: list[views.CVDetail]
    preferences: Preferences
    labels: list[CVLabels]
    runs: list[RunRecord]
    matches: list[Job]


LOCAL = {"127.0.0.1", "localhost", "testserver"}

# The pages (7.7): plain HTML, CSS and JavaScript, no build step (ui/).
UI = Path(__file__).parent / "ui"
# Only this server's own files may run, style or load on a page: no inline
# script or style, nothing from another site, not even the font.
PAGE_POLICY = (
    "default-src 'none'; script-src 'self'; style-src 'self'; font-src 'self'; "
    "img-src 'self' data:; connect-src 'self'; base-uri 'none'; "
    "form-action 'self'; frame-ancestors 'none'"
)


def page(name: str, lang: str, *, referrer: str = "same-origin") -> HTMLResponse:
    """A page from ui/, in the chosen language (api/language.py)."""
    html = (
        (UI / name)
        .read_text(encoding="utf-8")
        .replace("%LANG%", lang)
        .replace("%LANGUAGES%", ",".join(language.available()))
    )
    return HTMLResponse(
        html,
        headers={
            "Content-Security-Policy": PAGE_POLICY,
            "Referrer-Policy": referrer,
            "X-Content-Type-Options": "nosniff",
            "Cache-Control": "no-store",
        },
    )


def create_app(config: AppConfig) -> FastAPI:
    if not config.secure_cookies and not set(config.allowed_hosts) <= LOCAL:
        raise ValueError("a session cookie must be Secure on any host but this machine")

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
        version="7.7",
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
        answer = await call_next(request)
        # Answers carry CV text and matches: no browser or proxy keeps a copy
        # (a shared computer's cache would hand it to the next person).
        if request.url.path.startswith("/api/"):
            answer.headers.setdefault("Cache-Control", "no-store")
        return answer

    @app.exception_handler(ServiceError)
    async def service_error(request: Request, err: ServiceError) -> JSONResponse:
        """Whose fault it is decides the code: the CV's (422), a provider that
        is busy (503, try later), or one that refused or cannot be reached."""
        if isinstance(err, CVUnreadable):
            return JSONResponse({"detail": str(err)}, status_code=422)
        if isinstance(err, BudgetSpent):
            return JSONResponse({"detail": str(err)}, status_code=402)
        if isinstance(err, OwnKeysOff):
            return JSONResponse({"detail": str(err)}, status_code=503)
        busy = isinstance(err, ProviderRefused) and err.busy
        return JSONResponse(
            {"detail": str(err)},
            status_code=503 if busy else 502,
            headers={"Retry-After": "120"} if busy else None,
        )

    # -- who is asking ------------------------------------------------------

    def current_user(
        session: Annotated[str | None, Depends(SESSION_COOKIE)],
    ) -> User:
        """Whoever this request's session belongs to, or 401."""
        user = config.database.session_user(session) if session else None
        if user is None:
            raise HTTPException(
                401, "Not signed in. Open the login link you were sent."
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

    # -- the pages (7.7) ------------------------------------------------------

    @app.get("/", include_in_schema=False)
    def home(
        request: Request,
        session: Annotated[str | None, Depends(SESSION_COOKIE)],
    ) -> Response:
        """The dashboard; without a session, the way in."""
        user = config.database.session_user(session) if session else None
        if user is None:
            return RedirectResponse("/login", status_code=303)
        # A new account starts in the guide; one that finished or skipped it,
        # or already has a CV (accounts from before the guide), does not.
        store = config.database.store_for(user.id)
        if user.onboarded_at is None and store.active_cv() is None:
            return RedirectResponse("/guide", status_code=303)
        chosen = language.pick(request.headers.get("accept-language"), user.locale)
        return page("index.html", chosen)

    def signed_in_page(name: str):
        """A page that needs a session; without one, the way in."""

        def route(
            request: Request,
            session: Annotated[str | None, Depends(SESSION_COOKIE)],
        ) -> Response:
            user = config.database.session_user(session) if session else None
            if user is None:
                return RedirectResponse("/login", status_code=303)
            chosen = language.pick(request.headers.get("accept-language"), user.locale)
            return page(name, chosen)

        return route

    for path, name in (
        ("/guide", "guide.html"),
        ("/cv", "cv.html"),
        ("/preferences", "preferences.html"),
    ):
        app.add_api_route(
            path, signed_in_page(name), methods=["GET"], include_in_schema=False
        )

    @app.get("/login", include_in_schema=False)
    def login(request: Request) -> HTMLResponse:
        """What a login link opens (ui/login.html): a button, not a sign-in.
        No referrer, so the page's address never travels to another site."""
        chosen = language.pick(request.headers.get("accept-language"))
        return page("login.html", chosen, referrer="no-referrer")

    app.mount("/assets", StaticFiles(directory=UI / "assets"), name="assets")

    @app.post("/api/login")
    def sign_in(given: SignIn, response: Response) -> User:
        """Use a login link's code, once, and start a 30-day session."""
        try:
            session = config.database.redeem_login_link(given.token)
        except KeyError as err:
            raise HTTPException(
                401, "This link was used already or has expired. Ask for a new one."
            ) from err
        response.set_cookie(
            COOKIE,
            session,
            max_age=int(SESSION_VALID.total_seconds()),
            httponly=True,  # page scripts cannot read it
            secure=config.secure_cookies,
            samesite="lax",
            path="/",
        )
        return config.database.session_user(session)

    @app.post("/api/logout", status_code=204)
    def sign_out(
        response: Response,
        session: Annotated[str | None, Depends(SESSION_COOKIE)],
    ) -> None:
        if session:
            config.database.end_session(session)
        response.delete_cookie(COOKIE, path="/")

    @app.get("/api/me")
    def me(user: Me) -> User:
        return user

    @app.patch("/api/me")
    def update_me(user: Me, change: AboutMe) -> User:
        """Change your language or the name the page greets you with, or mark
        the guide as done so the dashboard stops sending you to it."""
        return config.database.update_user(
            user.id, **change.model_dump(exclude_none=True)
        )

    @app.get("/api/places")
    def places(user: Me) -> list[str]:
        """The Dutch places a home can be: what the home field suggests."""
        return list(place_names())

    @app.get("/api/dashboard")
    def my_dashboard(user: Me, store: Mine) -> views.Dashboard:
        """Everything the dashboard shows, in one answer."""
        return views.dashboard(
            user,
            store,
            corpus=config.corpus,
            models=config.models,
            database=config.database,
            vault=config.vault,
            budgets=config.budgets,
        )

    @app.get("/api/me/export")
    def export(user: Me, store: Mine) -> JSONResponse:
        """Everything JobLens keeps about you, as one JSON file to save."""
        everything = Everything(
            exported_at=datetime.now(UTC),
            user=user,
            cvs=[views.CVDetail.of(one) for one in store.cvs()],
            preferences=Preferences.model_validate(store.load_preferences() or {}),
            labels=store.labels(),
            runs=[store.load_run(one.id) for one in store.runs()],
            matches=store.jobs(limit=1000),
        )
        return JSONResponse(
            everything.model_dump(mode="json"),
            headers={
                "Content-Disposition": 'attachment; filename="joblens-my-data.json"'
            },
        )

    @app.post("/api/me/delete")
    def delete_me(user: Me, goodbye: Goodbye, response: Response) -> dict:
        """Delete your account and everything in it, now. Cannot be undone."""
        if goodbye.confirm != "delete everything":
            raise HTTPException(400, 'Send {"confirm": "delete everything"}.')
        config.database.delete_user(user.id)
        response.delete_cookie(COOKIE, path="/")
        return {"deleted": True}

    @app.get("/api/cvs")
    def cvs(store: Mine) -> list[views.CVSummary]:
        return [views.CVSummary.of(one) for one in store.cvs()]

    @app.get("/api/cvs/active")
    def active_cv(store: Mine) -> views.CVDetail:
        cv = store.active_cv()
        if cv is None:
            raise HTTPException(404, "No CV yet: upload one.")
        return views.CVDetail.of(cv)

    @app.post("/api/cvs", status_code=201)
    def upload_cv(
        user: Me,
        store: Mine,
        file: UploadFile,
        strip_name: Annotated[str | None, Form()] = None,
    ) -> views.CVDetail:
        """Read, redact and profile a CV (about half a cent), and make it the
        active one. `strip_name` is your name exactly as the CV writes it."""
        models, paid_by = paying(user, store, budget.UPLOAD_USD)
        # One byte over the limit is enough to know it is too big, without
        # reading a whole oversized file into memory.
        data = file.file.read(MAX_UPLOAD_BYTES + 1)

        def meter(prompt_tokens: int, output_tokens: int) -> None:
            config.database.record_usage(
                user.id,
                kind="cv",
                model=models.cv.model,
                prompt_tokens=prompt_tokens,
                output_tokens=output_tokens,
                cost_usd=cost_usd(models.cv, prompt_tokens, output_tokens),
                paid_by=paid_by,
            )

        record = add_cv(
            store,
            CVFile(file.filename or "cv", data),
            models,
            cache_dir=config.cache_dir,
            strip_name=(strip_name or "").strip() or None,
            chat=config.chat,
            meter=meter,
        )
        return views.CVDetail.of(record)

    @app.post("/api/cvs/{cv_id}/activate")
    def activate_cv(store: Mine, cv_id: str) -> views.CVDetail:
        try:
            return views.CVDetail.of(store.activate_cv(cv_id))
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
    def start_match(user: Me, store: Mine, ask: MatchAsk | None = None) -> Job:
        """Start matching the active CV with the saved preferences. Returns at
        once; follow the job at /api/matches/{id}. Costs about 4 cents."""
        ask = ask or MatchAsk()
        if store.active_cv() is None:
            raise HTTPException(409, "Upload a CV first: there is nothing to match.")
        models, paid_by = paying(user, store, budget.match_estimate(ask.top))
        try:
            job = store.start_job("match", ask.model_dump())
        except ValueError as err:
            raise HTTPException(409, str(err)) from err
        config.runner.submit(
            run_match_job,
            config.database,
            str(store.user_id),
            job.id,
            corpus=config.corpus,
            models=models,
            cache_dir=config.cache_dir,
            chat=config.chat,
            embed=config.embed,
            paid_by=paid_by,
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

    # -- whose model, and what it costs (7.6) --------------------------------

    def paying(user: User, store: PostgresStore, estimate: float) -> tuple[Models, str]:
        """This person's models for a paid step, after checking they may."""
        try:
            models, paid_by = ai.models_for(store, config.models, config.vault)
        except VaultError as err:
            raise HTTPException(409, str(err)) from err
        budget.check(config.database, user, paid_by, config.budgets, estimate)
        return models, paid_by

    @app.get("/api/ai")
    def my_ai(store: Mine) -> ai.AIChoice:
        """Whose model reads your CV and judges your matches, and which."""
        return ai.choice(store, config.models)

    @app.get("/api/ai/providers")
    def providers() -> list[dict]:
        """The providers you can bring a key for. The address is fixed per
        provider: that is where your CV text goes when you choose it."""
        return [
            asdict(one) | {"measured": MEASURED.get(f"{one.provider}/{one.suggested}")}
            for one in available(config.allow_local_providers)
        ]

    @app.put("/api/ai")
    def bring_key(store: Mine, given: BringKey) -> ai.AIChoice:
        """Use your own model. One tiny test call is made with it first."""
        try:
            return ai.bring_own_key(
                store,
                config.vault,
                provider=given.provider,
                model=given.model,
                api_key=given.api_key.get_secret_value(),
                thinking=given.thinking,
                allow_local=config.allow_local_providers,
                check=config.check_provider,
            )
        except ValueError as err:
            raise HTTPException(400, str(err)) from err

    @app.delete("/api/ai", status_code=204)
    def forget_key(store: Mine) -> None:
        """Go back to JobLens's model (with a monthly allowance for testers)."""
        store.delete_provider_key()

    @app.get("/api/usage")
    def my_usage(user: Me, store: Mine) -> budget.Usage:
        """What you spent this month, and what is left of a free allowance."""
        paid_by = "own" if store.provider_key() and config.vault else "operator"
        return budget.usage(config.database, user, paid_by, config.budgets)

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
