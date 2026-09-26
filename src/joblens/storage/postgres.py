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

import os
import uuid
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from psycopg_pool import ConnectionPool

from joblens.cv.read import CVFile
from joblens.cv.runs import RunRecord, digest
from joblens.cv.schema import CVProfile
from joblens.evals.matching import CVLabels
from joblens.storage.base import KEEP_ORIGINAL, CVRecord, Job, RunSummary, User
from joblens.storage.migrate import Migration, migrate


class Database:
    def __init__(self, url: str, *, pool_size: int = 0):
        """`pool_size` > 0 keeps that many connections open (the web server,
        7.4): opening one to Neon costs a TLS handshake, every request."""
        self.url = url
        self._pool = (
            ConnectionPool(
                url,
                min_size=1,
                max_size=pool_size,
                kwargs={"autocommit": True, "row_factory": dict_row},
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
    ) -> User:
        try:
            with self.connect() as conn:
                row = conn.execute(
                    "INSERT INTO users (email, display_name, locale) "
                    "VALUES (%s, %s, %s) RETURNING *",
                    (_email(email), display_name, locale),
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


def _id(value: str, what: str) -> uuid.UUID:
    """A uuid, or a KeyError: a malformed id names nothing, like a missing one."""
    try:
        return value if isinstance(value, uuid.UUID) else uuid.UUID(str(value))
    except ValueError as err:
        raise KeyError(f"no {what} {value!r}") from err


def _email(email: str | None) -> str | None:
    """One spelling per address, so Mahdi@ and mahdi@ are one account."""
    return email.strip().lower() if email else None


def _user(row: dict) -> User:
    return User.model_validate(row | {"id": str(row["id"])})


def _cv(row: dict) -> CVRecord:
    return CVRecord.model_validate(row | {"id": str(row["id"])})


def _job(row: dict) -> Job:
    return Job.model_validate(row | {"id": str(row["id"])})
