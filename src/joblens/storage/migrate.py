"""Bringing a database's tables up to date, by hand.

A migration is a numbered .sql file in schema/ (`0001_people_cvs_runs.sql`).
`migrate` applies every one the database has not seen yet, in order, each in
its own transaction, and writes its number into `schema_migrations`. The same
files make an empty Docker database and a Neon one identical, and a database
that is already up to date is left alone -- so it is safe to run on every
start.

**By hand, once.** Alembic does this and much more (it can write migrations by
comparing models to the database). What it does at the core is the forty lines
below, and CLAUDE.md's rule is to build a thing once before adopting a tool for
it. Plain SQL also means the schema file is the documentation: what you read is
exactly what the database runs.

Three rules the code keeps:

- **A lock.** Several sessions share this repo (and one database), and two
  processes starting at once would both see 0002 as missing and both apply it.
  `pg_advisory_lock` makes the second wait until the first is done, and then
  it finds nothing left to do.
- **An applied file never changes.** The database remembers a migration by its
  number, not its contents, so editing 0001 after it ran would give a fresh
  database a different schema from yours with nobody told. A checksum of each
  applied file is stored, and a mismatch stops everything. A change is a new
  file.
- **Code older than its database stops.** A database that has a migration this
  code does not know was upgraded by newer code; running old code against it
  is how a column gets written the old way.
"""

import hashlib
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import psycopg
from psycopg.rows import tuple_row

SCHEMA_DIR = Path(__file__).parent / "schema"

# Postgres keys advisory locks by an integer; any fixed number works, as long
# as nothing else in the database uses the same one.
LOCK = 70_200_001

TRACKING_TABLE = """
CREATE TABLE IF NOT EXISTS schema_migrations (
    version    integer PRIMARY KEY,
    name       text NOT NULL,
    checksum   text NOT NULL,
    applied_at timestamptz NOT NULL DEFAULT now()
)
"""


class MigrationError(RuntimeError):
    """The files and the database disagree about what the schema is."""


@dataclass(frozen=True)
class Migration:
    version: int
    name: str  # the file name, as it is shown and stored
    sql: str

    @property
    def checksum(self) -> str:
        return hashlib.sha256(self.sql.encode()).hexdigest()[:16]


@dataclass(frozen=True)
class Status:
    migration: Migration
    applied_at: datetime | None  # None: not applied to this database yet


def migrations(directory: Path = SCHEMA_DIR) -> list[Migration]:
    """Every migration file, in order of its number."""
    found: dict[int, Migration] = {}
    for path in sorted(directory.glob("*.sql")):
        number = path.stem.split("_", 1)[0]
        if not number.isdigit():
            raise MigrationError(f"{path.name}: a migration starts with its number")
        version = int(number)
        if version in found:
            raise MigrationError(
                f"{path.name} and {found[version].name} have the same number"
            )
        found[version] = Migration(version, path.name, path.read_text("utf-8"))
    return [found[version] for version in sorted(found)]


def migrate(conn: psycopg.Connection, directory: Path = SCHEMA_DIR) -> list[Migration]:
    """Apply what this database is missing. Returns what was applied now.

    `conn` must be in autocommit mode, so that each migration is a
    transaction of its own and the lock outlives them all.
    """
    wanted = migrations(directory)
    conn.execute("SELECT pg_advisory_lock(%s)", (LOCK,))
    try:
        with conn.transaction():
            conn.execute(TRACKING_TABLE)
        applied = _applied(conn)
        _check(wanted, applied)
        fresh = [one for one in wanted if one.version not in applied]
        for one in fresh:
            with conn.transaction():
                conn.execute(one.sql)
                conn.execute(
                    "INSERT INTO schema_migrations (version, name, checksum) "
                    "VALUES (%s, %s, %s)",
                    (one.version, one.name, one.checksum),
                )
        return fresh
    finally:
        conn.execute("SELECT pg_advisory_unlock(%s)", (LOCK,))


def status(conn: psycopg.Connection, directory: Path = SCHEMA_DIR) -> list[Status]:
    """Each migration file, and when this database applied it."""
    rows = _rows(conn, "SELECT to_regclass('schema_migrations') IS NOT NULL")
    when = {}
    if rows[0][0]:
        when = dict(_rows(conn, "SELECT version, applied_at FROM schema_migrations"))
    return [Status(one, when.get(one.version)) for one in migrations(directory)]


def _applied(conn: psycopg.Connection) -> dict[int, tuple[str, str]]:
    rows = _rows(conn, "SELECT version, name, checksum FROM schema_migrations")
    return {version: (name, checksum) for version, name, checksum in rows}


def _rows(conn: psycopg.Connection, query: str) -> list[tuple]:
    """Plain tuples, whatever rows the connection hands out by default."""
    with conn.cursor(row_factory=tuple_row) as cursor:
        return cursor.execute(query).fetchall()


def _check(wanted: list[Migration], applied: dict[int, tuple[str, str]]) -> None:
    known = {one.version: one for one in wanted}
    unknown = sorted(set(applied) - set(known))
    if unknown:
        names = ", ".join(applied[version][0] for version in unknown)
        raise MigrationError(
            f"this database has migrations this code does not: {names}. "
            "It was upgraded by newer code; update this checkout first."
        )
    for version, (name, checksum) in applied.items():
        if known[version].checksum != checksum:
            raise MigrationError(
                f"{name} was changed after it was applied to this database. An "
                "applied migration never changes: put the change in a new file."
            )
