"""The store, backed by Postgres: FileStore's promises, for more than one person.

Two classes. `Database` is the whole database: it migrates, creates and deletes
users, and hands out one person's store. `PostgresStore` is that person's runs,
labels, preferences and CVs -- and every query it sends says `user_id = %s`,
so a store cannot read or write anybody else's rows, whatever id it is handed.

**Local and hosted are one code path.** Docker's Postgres on this machine
(compose.yaml) and Neon in production run the same migrations and answer the
same SQL; only DATABASE_URL differs. The tests run against the Docker one.

**SQL by hand.** No ORM: each method is the query it sends, next to the model
it fills. Every value travels as a parameter (`%s`), never pasted into the SQL
string, so a CV called `'); DROP TABLE users; --` is a CV with a strange name.
Documents that already have a shape in code -- a run, a label file, a profile
-- are stored as JSONB and read back through the same pydantic model that wrote
them, which is also what keeps a JSON column honest.

**A connection per call, for now.** Opening one costs a few milliseconds
locally and a little more on Neon; the scripts make a handful of calls. The web
server in 7.4 replaces `connect` with a pool and nothing else changes.
"""

import hashlib
import logging
import os
import secrets
import threading
import uuid
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from psycopg_pool import ConnectionPool

from joblens.cv.read import CVFile
from joblens.cv.runs import RunRecord, digest
from joblens.cv.schema import CVProfile
from joblens.evals.matching import CVLabels
from joblens.storage.base import (
    KEEP_ORIGINAL,
    CVRecord,
    Job,
    NightlyRun,
    Person,
    ProviderKey,
    RunSummary,
    User,
)
from joblens.storage.migrate import Migration, migrate

logger = logging.getLogger(__name__)

# A login link is sent by hand to an invited tester: a week to open it. A
# session lasts a month, then the person asks for a new link.
LOGIN_LINK_VALID = timedelta(days=7)
SESSION_VALID = timedelta(days=30)
# One nightly run at a time (7.8.5): Cloud Scheduler delivers at least once,
# and was seen delivering one start twice, 30 seconds apart. Beside
# migrate.LOCK (70_200_001) and the tests' (70_200_002).
NIGHTLY_LOCK = 70_200_003
# What the admin page lists of past runs (7.10.1): older ones are deleted.
NIGHTLY_KEPT = timedelta(days=90)
# A run the admin page asked for shows as starting until its row appears, or
# for this long: Cloud Run starts one in about a minute, and one that never
# came must not block the button for ever (7.10.2).
NIGHTLY_STARTING = timedelta(minutes=10)


class Database:
    def __init__(self, url: str, *, pool_size: int = 0):
        """`pool_size` > 0 keeps that many connections open (the web server,
        7.4): opening one to Neon costs a TLS handshake, every request.

        Neon's free plan suspends the database after about five idle minutes
        and cuts every connection ("terminating connection due to
        administrator command", seen 2026-09-29 on the hosted app). So a
        connection is checked before it is handed out, and one idle for four
        minutes is closed before Neon does it: the first request after a
        quiet spell gets a live connection, not an error."""
        self.url = url
        self._pool = (
            ConnectionPool(
                url,
                min_size=1,
                max_size=pool_size,
                kwargs={"autocommit": True, "row_factory": dict_row},
                check=ConnectionPool.check_connection,
                max_idle=240,
                open=True,
            )
            if pool_size
            else None
        )

    def close(self) -> None:
        if self._pool is not None:
            self._pool.close()

    @classmethod
    def from_env(
        cls, env: Mapping[str, str] | None = None, *, pool_size: int = 0
    ) -> "Database":
        """From DATABASE_URL. Scripts call load_dotenv() before this."""
        url = (env if env is not None else os.environ).get("DATABASE_URL")
        if not url:
            raise ValueError(
                "DATABASE_URL is not set. For the local database: docker compose "
                "up -d db, and copy the DATABASE_URL line from .env.example."
            )
        return cls(url, pool_size=pool_size)

    @contextmanager
    def connect(self) -> Iterator[psycopg.Connection]:
        """Autocommit: a statement is its own transaction unless a method opens
        one with `conn.transaction()`, which the multi-statement ones do."""
        if self._pool is not None:
            with self._pool.connection() as conn:
                yield conn
            return
        with psycopg.connect(self.url, autocommit=True, row_factory=dict_row) as conn:
            yield conn

    def migrate(self) -> list[Migration]:
        with self.connect() as conn:
            return migrate(conn)

    # -- people -------------------------------------------------------------

    def create_user(
        self,
        *,
        email: str | None = None,
        display_name: str | None = None,
        locale: str | None = None,
        role: str = "tester",
    ) -> User:
        try:
            with self.connect() as conn:
                row = conn.execute(
                    "INSERT INTO users (email, display_name, locale, role) "
                    "VALUES (%s, %s, %s, %s) RETURNING *",
                    (_email(email), display_name, locale, role),
                ).fetchone()
        except psycopg.errors.UniqueViolation as err:
            raise ValueError(f"there is already an account for {email}") from err
        return _user(row)

    def user(self, user_id: str) -> User:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT * FROM users WHERE id = %s", (_id(user_id, "user"),)
            ).fetchone()
        if row is None:
            raise KeyError(f"no user {user_id!r}")
        return _user(row)

    def user_by_email(self, email: str) -> User | None:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT * FROM users WHERE email = %s", (_email(email),)
            ).fetchone()
        return _user(row) if row else None

    def update_user(
        self,
        user_id: str,
        *,
        locale: str | None = None,
        display_name: str | None = None,
        onboarded: bool = False,
    ) -> User:
        """Change what a person may change themselves; None leaves it as is.
        `onboarded` marks the guide done (or skipped), once: the first time
        is the one kept."""
        with self.connect() as conn:
            row = conn.execute(
                "UPDATE users SET locale = coalesce(%s, locale), "
                "display_name = coalesce(%s, display_name), "
                "onboarded_at = CASE WHEN %s THEN coalesce(onboarded_at, now()) "
                "ELSE onboarded_at END "
                "WHERE id = %s RETURNING *",
                (locale, display_name, onboarded, _id(user_id, "user")),
            ).fetchone()
        if row is None:
            raise KeyError(f"no user {user_id!r}")
        return _user(row)

    def set_role(self, user_id: str, role: str) -> User:
        """Owner or tester; the database refuses anything else."""
        with self.connect() as conn:
            row = conn.execute(
                "UPDATE users SET role = %s WHERE id = %s RETURNING *",
                (role, _id(user_id, "user")),
            ).fetchone()
        if row is None:
            raise KeyError(f"no user {user_id!r}")
        return _user(row)

    def delete_user(self, user_id: str) -> bool:
        """The "delete my data" button: a person and all they stored, at once.

        One statement, because every table that holds a person's data points
        at `users` with ON DELETE CASCADE (schema/0001). A table added later
        without that clause would make this fail loudly rather than leave
        rows behind, because the foreign key refuses the delete.
        """
        with self.connect() as conn:
            deleted = conn.execute(
                "DELETE FROM users WHERE id = %s", (_id(user_id, "user"),)
            ).rowcount
        return deleted == 1

    # -- signing in (7.5) ---------------------------------------------------

    def create_login_link(
        self, user_id: str, valid_for: timedelta = LOGIN_LINK_VALID
    ) -> str:
        """A one-time login token for this person. Only its hash is kept, so
        this return value is the only copy: print it, send it, forget it."""
        token = secrets.token_urlsafe(32)
        with self.connect() as conn:
            conn.execute(
                "INSERT INTO login_links (token_hash, user_id, expires_at) "
                "VALUES (%s, %s, now() + %s)",
                (_hash(token), _id(user_id, "user"), valid_for),
            )
        return token

    def invite(
        self,
        email: str,
        *,
        display_name: str | None = None,
        locale: str | None = None,
    ) -> tuple[User, str, bool]:
        """An account for `email` if there is none, and a new login link.

        Returns the person, the link's token and whether the account is new.
        A new account is a tester; the owner is made with `db.py set-role`,
        never from a page. An existing account keeps its name and language.

        One waiting link per person: a new link replaces any unused one, so a
        link sent to the wrong chat stops working once its replacement is made.
        """
        user, created = self.user_by_email(email), False
        if user is None:
            try:
                user = self.create_user(
                    email=email, display_name=display_name, locale=locale
                )
                created = True
            except ValueError:  # made by a second click in the same instant
                user = self.user_by_email(email)
        with self.connect() as conn:
            conn.execute(
                "DELETE FROM login_links WHERE user_id = %s AND used_at IS NULL",
                (_id(user.id, "user"),),
            )
        return user, self.create_login_link(user.id), created

    def people(self) -> list[Person]:
        """Every account, newest first, with whether a link is waiting and
        whether a session still works: the owner's invite list (7.9.1)."""
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT u.id, u.email, u.display_name, u.role, u.created_at, "
                "(SELECT max(l.expires_at) FROM login_links l WHERE l.user_id = u.id "
                " AND l.used_at IS NULL AND l.expires_at > now()) AS link_until, "
                "(SELECT max(s.created_at) FROM sessions s WHERE s.user_id = u.id "
                " AND s.expires_at > now()) AS signed_in_at "
                "FROM users u ORDER BY u.created_at DESC"
            ).fetchall()
        return [Person.model_validate(row | {"id": str(row["id"])}) for row in rows]

    def redeem_login_link(
        self, token: str, session_for: timedelta = SESSION_VALID
    ) -> str:
        """Use a link, once, and start a session. Returns the session token.

        Marking the link used and starting the session is one transaction, and
        the UPDATE only matches an unused, unexpired link: two tabs opening the
        same link at once get one session and one refusal, never two sessions.
        """
        with self.connect() as conn, conn.transaction():
            row = conn.execute(
                "UPDATE login_links SET used_at = now() WHERE token_hash = %s "
                "AND used_at IS NULL AND expires_at > now() RETURNING user_id",
                (_hash(token),),
            ).fetchone()
            if row is None:
                raise KeyError("this link was used already, or has expired")
            session = secrets.token_urlsafe(32)
            conn.execute(
                "INSERT INTO sessions (token_hash, user_id, expires_at) "
                "VALUES (%s, %s, now() + %s)",
                (_hash(session), row["user_id"], session_for),
            )
        return session

    def session_user(self, session: str) -> User | None:
        """Who a session belongs to, while it is valid; None otherwise."""
        with self.connect() as conn:
            row = conn.execute(
                "SELECT u.* FROM sessions s JOIN users u ON u.id = s.user_id "
                "WHERE s.token_hash = %s AND s.expires_at > now()",
                (_hash(session),),
            ).fetchone()
        return _user(row) if row else None

    def end_session(self, session: str) -> None:
        with self.connect() as conn:
            conn.execute(
                "DELETE FROM sessions WHERE token_hash = %s", (_hash(session),)
            )

    def nightly_enabled(self) -> bool:
        """Whether the nightly job fetches vacancies (7.8.5). Off until the
        owner switches it on in settings: a missing row is off."""
        with self.connect() as conn:
            row = conn.execute(
                "SELECT value FROM app_settings WHERE key = 'nightly_fetch'"
            ).fetchone()
        return bool(row and row["value"].get("enabled"))

    def set_nightly(self, enabled: bool) -> datetime:
        """Switch the nightly fetch on or off; returns when it was switched."""
        with self.connect() as conn:
            row = conn.execute(
                "INSERT INTO app_settings (key, value) VALUES ('nightly_fetch', %s) "
                "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, "
                "updated_at = now() RETURNING updated_at",
                (Jsonb({"enabled": enabled}),),
            ).fetchone()
        return row["updated_at"]

    @contextmanager
    def nightly_lock(self, keepalive: float = 60) -> Iterator[bool]:
        """Hold the one-run-at-a-time lock while the block runs. Yields False,
        without waiting, when another run holds it. On a connection of its
        own, so it lasts the whole run -- and goes when the process does, so
        a run that crashed blocks nobody.

        That connection says nothing while the fetch runs, and Neon cuts a
        connection that is quiet for a few minutes (measured 2026-09-30: cut
        after 7 idle minutes, "terminating connection due to administrator
        command"); the lock would go with it, halfway through the run. So
        while it is held, a thread asks it something every `keepalive`
        seconds."""
        with psycopg.connect(self.url, autocommit=True) as conn:
            got = conn.execute(
                "SELECT pg_try_advisory_lock(%s)", (NIGHTLY_LOCK,)
            ).fetchone()[0]
            stop = threading.Event()
            beat = threading.Thread(
                target=_keep_alive, args=(conn, stop, keepalive), daemon=True
            )
            if got:
                beat.start()
            try:
                yield got
            finally:
                if got:
                    stop.set()
                    beat.join()
                    try:
                        conn.execute("SELECT pg_advisory_unlock(%s)", (NIGHTLY_LOCK,))
                    except psycopg.Error:
                        pass  # the connection is gone, and the lock with it

    def nightly_switched_at(self) -> datetime | None:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT updated_at FROM app_settings WHERE key = 'nightly_fetch'"
            ).fetchone()
        return row["updated_at"] if row else None

    # -- what each nightly run did (7.10.1) ------------------------------------

    def start_nightly(
        self,
        trigger: str,
        *,
        fetch: bool,
        source: str | None = None,
        execution: str | None = None,
    ) -> int:
        """A row for a run that is starting; returns its id. Runs older than
        NIGHTLY_KEPT are deleted at the same time."""
        with self.connect() as conn:
            conn.execute(
                "DELETE FROM nightly_runs WHERE started_at < now() - %s",
                (NIGHTLY_KEPT,),
            )
            row = conn.execute(
                "INSERT INTO nightly_runs (trigger, fetched, source, execution) "
                "VALUES (%s, %s, %s, %s) RETURNING id",
                (trigger, fetch, source, execution),
            ).fetchone()
        return row["id"]

    def request_nightly(self) -> datetime:
        """The admin page asked Cloud Run for a run (7.10.2): noted, so the
        page can say it is starting until the run writes its own row."""
        with self.connect() as conn:
            row = conn.execute(
                "INSERT INTO app_settings (key, value) VALUES "
                "('nightly_requested', '{}') ON CONFLICT (key) DO UPDATE "
                "SET updated_at = now() RETURNING updated_at"
            ).fetchone()
        return row["updated_at"]

    def nightly_requested(self) -> datetime | None:
        """When the page asked for a run that has not started yet, if it did
        within NIGHTLY_STARTING; None otherwise."""
        with self.connect() as conn:
            row = conn.execute(
                "SELECT s.updated_at FROM app_settings s "
                "WHERE s.key = 'nightly_requested' AND s.updated_at > now() - %s "
                "AND NOT EXISTS (SELECT 1 FROM nightly_runs r "
                "WHERE r.started_at >= s.updated_at)",
                (NIGHTLY_STARTING,),
            ).fetchone()
        return row["updated_at"] if row else None

    def finish_nightly(
        self,
        run_id: int,
        *,
        steps: dict[str, bool],
        new_vacancies: int | None,
        problems: list[str],
        overview: dict | None,
    ) -> None:
        """What a run did, once it is done. `overview` is sources/overview.py's
        Overview as JSON, or None when it could not be read."""
        in_joblens = (
            sum(source["in_joblens"] for source in overview["sources"])
            if overview
            else None
        )
        with self.connect() as conn:
            conn.execute(
                "UPDATE nightly_runs SET finished_at = now(), steps = %s, "
                "new_vacancies = %s, problems = %s, in_joblens = %s, overview = %s "
                "WHERE id = %s",
                (
                    Jsonb(steps),
                    new_vacancies,
                    Jsonb(problems),
                    in_joblens,
                    Jsonb(overview) if overview is not None else None,
                    run_id,
                ),
            )

    def nightly_runs(self, limit: int = 20) -> list[NightlyRun]:
        """The newest runs first, without their overview."""
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT id, started_at, finished_at, trigger, fetched, steps, "
                "new_vacancies, problems, in_joblens, source, execution "
                "FROM nightly_runs "
                "ORDER BY started_at DESC, id DESC LIMIT %s",
                (limit,),
            ).fetchall()
        return [NightlyRun.model_validate(row) for row in rows]

    def nightly_overview(self) -> tuple[datetime, dict] | None:
        """The sources as the newest run that could read them left them: when
        it finished, and sources/overview.py's Overview as JSON."""
        with self.connect() as conn:
            row = conn.execute(
                "SELECT finished_at, overview FROM nightly_runs "
                "WHERE overview IS NOT NULL ORDER BY started_at DESC, id DESC LIMIT 1"
            ).fetchone()
        return (row["finished_at"], row["overview"]) if row else None

    def nightly_running(self) -> bool:
        """Whether a run holds the lock right now. Read from pg_locks, never by
        trying the lock: a try that got it would hold it for a moment, and a
        run starting in that moment would think another was going. A bigint
        advisory key shows as classid (high half), objid (low half) and
        objsubid 1. pg_locks lists every database on the server, and an
        advisory lock belongs to one, so only this database's count."""
        with self.connect() as conn:
            row = conn.execute(
                "SELECT EXISTS (SELECT 1 FROM pg_locks WHERE locktype = 'advisory' "
                "AND database = (SELECT oid FROM pg_database "
                "WHERE datname = current_database()) "
                "AND classid::bigint = %s AND objid::bigint = %s AND objsubid = 1 "
                "AND granted) AS held",
                (NIGHTLY_LOCK >> 32, NIGHTLY_LOCK & 0xFFFFFFFF),
            ).fetchone()
        return row["held"]

    def purge_expired_logins(self) -> int:
        """Sessions and links past their date; `db.py purge-files` runs it."""
        with self.connect() as conn:
            sessions = conn.execute(
                "DELETE FROM sessions WHERE expires_at <= now()"
            ).rowcount
            links = conn.execute(
                "DELETE FROM login_links WHERE expires_at <= now()"
            ).rowcount
        return sessions + links

    def store_for(self, user_id: str) -> "PostgresStore":
        """One person's store. Refuses an id that is not a user."""
        return PostgresStore(self, self.user(user_id).id)

    def update_job(self, job_id: str, **fields) -> None:
        """Write a job's progress or outcome. Called by the worker, which is
        not a person's store: it holds a job id, handed over by the request
        that created the job for that person."""
        allowed = {"status", "stage", "done", "total", "run_id", "error"}
        unknown = set(fields) - allowed
        if unknown:
            raise ValueError(f"not a job field: {', '.join(sorted(unknown))}")
        # The column names come from the fixed set above, never from input;
        # the values travel as parameters like everywhere else.
        columns = ", ".join(f"{name} = %s" for name in fields)
        with self.connect() as conn:
            conn.execute(
                f"UPDATE jobs SET {columns}, updated_at = now() WHERE id = %s",
                (*fields.values(), _id(job_id, "job")),
            )

    def fail_interrupted_jobs(self) -> int:
        """Jobs left open by a server that stopped: say so, and free the slot.

        Run when a server starts. A job cannot survive its process -- it is a
        thread in it -- so an open one found at start-up was cut off, and
        leaving it open would block that person's next match for ever.
        """
        with self.connect() as conn:
            return conn.execute(
                "UPDATE jobs SET status = 'failed', updated_at = now(), "
                "error = 'The server restarted before this finished. Start it again.' "
                "WHERE status IN ('queued', 'running')"
            ).rowcount

    # -- what paid calls cost (7.6) --------------------------------------------

    def record_usage(
        self,
        user_id: str,
        *,
        kind: str,
        model: str,
        prompt_tokens: int,
        output_tokens: int,
        cost_usd: float | None,
        paid_by: str,
    ) -> None:
        with self.connect() as conn:
            conn.execute(
                "INSERT INTO usage (user_id, kind, model, prompt_tokens, "
                "output_tokens, cost_usd, paid_by) VALUES (%s, %s, %s, %s, %s, %s, %s)",
                (
                    _id(user_id, "user"),
                    kind,
                    model,
                    prompt_tokens,
                    output_tokens,
                    cost_usd,
                    paid_by,
                ),
            )

    def spent_this_month(self, user_id: str | None = None) -> dict[str, float]:
        """Dollars spent since the first of this month (UTC), by who paid.

        For one person, or, without `user_id`, for everybody: the second is
        what the cap on JobLens's own key is checked against. A call whose
        model has no known price counts as nothing -- which is why the budget
        is only offered on JobLens's key, whose model is priced.
        """
        where, values = "at >= date_trunc('month', now() AT TIME ZONE 'UTC')", []
        if user_id is not None:
            where += " AND user_id = %s"
            values.append(_id(user_id, "user"))
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT paid_by, coalesce(sum(cost_usd), 0) AS usd FROM usage "
                f"WHERE {where} GROUP BY paid_by",
                values,
            ).fetchall()
        spent = {"operator": 0.0, "own": 0.0}
        spent.update({row["paid_by"]: float(row["usd"]) for row in rows})
        return spent

    def purge_expired_files(self, now: datetime | None = None) -> int:
        """Delete uploaded files past their expiry. Returns how many went.

        The CV's text, profile and history stay: only the unredacted file,
        which was kept so it could be read again, is gone.
        """
        with self.connect() as conn:
            return conn.execute(
                "DELETE FROM cv_files WHERE expires_at <= %s",
                (now or datetime.now(UTC),),
            ).rowcount


class PostgresStore:
    """One person's runs, labels, preferences and CVs (storage/base.py)."""

    def __init__(self, db: Database, user_id: str):
        self.db = db
        self.user_id = _id(user_id, "user")

    # -- runs ---------------------------------------------------------------

    def runs(self) -> list[RunSummary]:
        with self.db.connect() as conn:
            rows = conn.execute(
                "SELECT id, cv_name AS cv, at, corpus, corpus_size, judged, "
                "ranked, outcome, cost_usd FROM runs WHERE user_id = %s "
                "ORDER BY at DESC, id DESC",
                (self.user_id,),
            ).fetchall()
        return [RunSummary.model_validate(row) for row in rows]

    def load_run(self, run_id: str) -> RunRecord:
        with self.db.connect() as conn:
            row = conn.execute(
                "SELECT record FROM runs WHERE user_id = %s AND id = %s",
                (self.user_id, run_id),
            ).fetchone()
        if row is None:
            raise KeyError(f"no run {run_id!r}")
        return RunRecord.model_validate(row["record"])

    def save_run(self, record: RunRecord) -> str:
        """Under FileStore's minute-stamped id, and never on top of another.

        The primary key decides: an insert that finds the id taken does
        nothing, and the next suffix is tried. Two writers in the same minute
        cannot both win, which a check-then-write could not promise.
        """
        stem = f"{record.stamp.at:%Y-%m-%d_%H%M}_{record.stamp.cv_name}"
        run_id, attempt = stem, 2
        with self.db.connect() as conn:
            while True:
                inserted = conn.execute(
                    "INSERT INTO runs (user_id, id, cv_name, at, corpus, "
                    "corpus_size, judged, ranked, outcome, cost_usd, record) "
                    "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) "
                    "ON CONFLICT (user_id, id) DO NOTHING",
                    (
                        self.user_id,
                        run_id,
                        record.stamp.cv_name,
                        record.stamp.at,
                        record.stamp.corpus,
                        record.stamp.corpus_size,
                        len(record.rows),
                        len(record.ranking),
                        record.outcome.value,
                        record.cost_usd,
                        Jsonb(record.model_dump(mode="json")),
                    ),
                ).rowcount
                if inserted:
                    return run_id
                run_id, attempt = f"{stem}-{attempt}", attempt + 1

    # -- labels -------------------------------------------------------------

    def labels(self) -> list[CVLabels]:
        with self.db.connect() as conn:
            rows = conn.execute(
                "SELECT labels FROM labels WHERE user_id = %s ORDER BY cv",
                (self.user_id,),
            ).fetchall()
        return [CVLabels.model_validate(row["labels"]) for row in rows]

    def load_labels(self, cv: str) -> CVLabels | None:
        with self.db.connect() as conn:
            row = conn.execute(
                "SELECT labels FROM labels WHERE user_id = %s AND cv = %s",
                (self.user_id, cv),
            ).fetchone()
        return CVLabels.model_validate(row["labels"]) if row else None

    def save_labels(self, labels: CVLabels) -> str:
        with self.db.connect() as conn:
            conn.execute(
                "INSERT INTO labels (user_id, cv, labels) VALUES (%s, %s, %s) "
                "ON CONFLICT (user_id, cv) DO UPDATE "
                "SET labels = EXCLUDED.labels, updated_at = now()",
                (self.user_id, labels.cv, Jsonb(labels.model_dump(mode="json"))),
            )
        return f"labels of {labels.cv}"

    # -- preferences --------------------------------------------------------

    def load_preferences(self) -> dict | None:
        with self.db.connect() as conn:
            row = conn.execute(
                "SELECT answers FROM preferences WHERE user_id = %s",
                (self.user_id,),
            ).fetchone()
        return row["answers"] if row else None

    def save_preferences(self, values: dict) -> str:
        with self.db.connect() as conn:
            conn.execute(
                "INSERT INTO preferences (user_id, answers) VALUES (%s, %s) "
                "ON CONFLICT (user_id) DO UPDATE "
                "SET answers = EXCLUDED.answers, updated_at = now()",
                (self.user_id, Jsonb(values)),
            )
        return "preferences"

    # -- CVs ----------------------------------------------------------------

    def add_cv(
        self,
        filename: str,
        text: str,
        *,
        profile: CVProfile | None = None,
        strip_name: str | None = None,
        original: bytes | None = None,
        removed: dict[str, int] | None = None,
        at: datetime | None = None,
    ) -> CVRecord:
        # The last part of the name only, as read_cv does with an upload.
        name = Path(Path(filename).name)
        uploaded = at or datetime.now(UTC)
        with self.db.connect() as conn, conn.transaction():
            conn.execute(
                "UPDATE cvs SET active = false WHERE user_id = %s AND active",
                (self.user_id,),
            )
            row = conn.execute(
                "INSERT INTO cvs (user_id, name, filename, strip_name, text, "
                "digest, profile, removed, uploaded_at, active) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, true) RETURNING *",
                (
                    self.user_id,
                    name.stem,
                    name.name,
                    strip_name,
                    text,
                    digest(text),
                    Jsonb(profile.model_dump(mode="json")) if profile else None,
                    Jsonb(removed or {}),
                    uploaded,
                ),
            ).fetchone()
            if original is not None:
                conn.execute(
                    "INSERT INTO cv_files (cv_id, data, expires_at) "
                    "VALUES (%s, %s, %s)",
                    (row["id"], original, uploaded + KEEP_ORIGINAL),
                )
        return _cv(row)

    def cvs(self) -> list[CVRecord]:
        with self.db.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM cvs WHERE user_id = %s ORDER BY uploaded_at DESC, id",
                (self.user_id,),
            ).fetchall()
        return [_cv(row) for row in rows]

    def active_cv(self) -> CVRecord | None:
        with self.db.connect() as conn:
            row = conn.execute(
                "SELECT * FROM cvs WHERE user_id = %s AND active", (self.user_id,)
            ).fetchone()
        return _cv(row) if row else None

    def load_cv(self, cv_id: str) -> CVRecord:
        with self.db.connect() as conn:
            row = conn.execute(
                "SELECT * FROM cvs WHERE user_id = %s AND id = %s",
                (self.user_id, _id(cv_id, "CV")),
            ).fetchone()
        if row is None:
            raise KeyError(f"no CV {cv_id!r}")
        return _cv(row)

    def activate_cv(self, cv_id: str) -> CVRecord:
        wanted = _id(cv_id, "CV")
        with self.db.connect() as conn, conn.transaction():
            # Checked inside the transaction, so a CV that is not this
            # person's is refused before anything of theirs is changed.
            found = conn.execute(
                "SELECT 1 FROM cvs WHERE user_id = %s AND id = %s",
                (self.user_id, wanted),
            ).fetchone()
            if found is None:
                raise KeyError(f"no CV {cv_id!r}")
            conn.execute(
                "UPDATE cvs SET active = false WHERE user_id = %s AND active",
                (self.user_id,),
            )
            row = conn.execute(
                "UPDATE cvs SET active = true WHERE user_id = %s AND id = %s "
                "RETURNING *",
                (self.user_id, wanted),
            ).fetchone()
        return _cv(row)

    def original(self, cv_id: str) -> CVFile | None:
        with self.db.connect() as conn:
            row = conn.execute(
                "SELECT c.filename, f.data FROM cv_files f "
                "JOIN cvs c ON c.id = f.cv_id "
                "WHERE c.user_id = %s AND c.id = %s AND f.expires_at > now()",
                (self.user_id, _id(cv_id, "CV")),
            ).fetchone()
        return CVFile(row["filename"], bytes(row["data"])) if row else None

    # -- their own model (7.6) ---------------------------------------------

    def provider_key(self) -> ProviderKey | None:
        with self.db.connect() as conn:
            row = conn.execute(
                "SELECT * FROM provider_keys WHERE user_id = %s", (self.user_id,)
            ).fetchone()
        if row is None:
            return None
        return ProviderKey.model_validate(
            row | {"key_secret": bytes(row["key_secret"])}
        )

    def save_provider_key(
        self,
        *,
        provider: str,
        model: str,
        thinking: bool,
        key_secret: bytes,
        key_hint: str,
    ) -> ProviderKey:
        """Replace this person's own model. Called after a test call succeeded."""
        with self.db.connect() as conn:
            conn.execute(
                "INSERT INTO provider_keys (user_id, provider, model, thinking, "
                "key_secret, key_hint, verified_at) "
                "VALUES (%s, %s, %s, %s, %s, %s, now()) "
                "ON CONFLICT (user_id) DO UPDATE SET provider = EXCLUDED.provider, "
                "model = EXCLUDED.model, thinking = EXCLUDED.thinking, "
                "key_secret = EXCLUDED.key_secret, key_hint = EXCLUDED.key_hint, "
                "verified_at = now(), updated_at = now()",
                (self.user_id, provider, model, thinking, key_secret, key_hint),
            )
        return self.provider_key()

    def delete_provider_key(self) -> bool:
        with self.db.connect() as conn:
            return (
                conn.execute(
                    "DELETE FROM provider_keys WHERE user_id = %s", (self.user_id,)
                ).rowcount
                == 1
            )

    # -- jobs ---------------------------------------------------------------

    def start_job(self, kind: str, request: dict) -> Job:
        try:
            with self.db.connect() as conn:
                row = conn.execute(
                    "INSERT INTO jobs (user_id, kind, request) VALUES (%s, %s, %s) "
                    "RETURNING *",
                    (self.user_id, kind, Jsonb(request)),
                ).fetchone()
        except psycopg.errors.UniqueViolation as err:
            raise ValueError(
                "a match is already running for you: wait for it to finish"
            ) from err
        return _job(row)

    def job(self, job_id: str) -> Job:
        with self.db.connect() as conn:
            row = conn.execute(
                "SELECT * FROM jobs WHERE user_id = %s AND id = %s",
                (self.user_id, _id(job_id, "job")),
            ).fetchone()
        if row is None:
            raise KeyError(f"no job {job_id!r}")
        return _job(row)

    def jobs(self, limit: int = 20) -> list[Job]:
        with self.db.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM jobs WHERE user_id = %s "
                "ORDER BY created_at DESC LIMIT %s",
                (self.user_id, limit),
            ).fetchall()
        return [_job(row) for row in rows]


def _keep_alive(conn: psycopg.Connection, stop: threading.Event, every: float) -> None:
    """Keep a connection from going quiet until `stop` (Database.nightly_lock).
    A psycopg connection may be shared between threads: it serialises them."""
    while not stop.wait(every):
        try:
            conn.execute("SELECT 1")
        except psycopg.Error:
            logger.warning("the nightly lock's connection was lost; so was the lock")
            return


def _id(value: str, what: str) -> uuid.UUID:
    """A uuid, or a KeyError: a malformed id names nothing, like a missing one."""
    try:
        return value if isinstance(value, uuid.UUID) else uuid.UUID(str(value))
    except ValueError as err:
        raise KeyError(f"no {what} {value!r}") from err


def _hash(token: str) -> str:
    """What the database keeps of a login link or a session: never the token."""
    return hashlib.sha256(token.encode()).hexdigest()


def _email(email: str | None) -> str | None:
    """One spelling per address, so Mahdi@ and mahdi@ are one account."""
    return email.strip().lower() if email else None


def _user(row: dict) -> User:
    return User.model_validate(row | {"id": str(row["id"])})


def _cv(row: dict) -> CVRecord:
    return CVRecord.model_validate(row | {"id": str(row["id"])})


def _job(row: dict) -> Job:
    return Job.model_validate(row | {"id": str(row["id"])})
