"""The owner's switch for the nightly update (7.8.5): off by default, only the
owner can see or flip it, and the admin page shows the last update."""

import time

import pytest
from test_api import app_for
from test_published import MODEL, corpus_and_vectors

from joblens.storage.postgres import NIGHTLY_LOCK
from joblens.storage.published import publish

# The backend that holds the nightly lock, in this database: pg_locks lists
# every database on the server, and other checkouts may hold theirs.
HOLDER = (
    "SELECT pid FROM pg_locks WHERE locktype = 'advisory' AND objid::bigint = %s "
    "AND database = (SELECT oid FROM pg_database WHERE datname = current_database())"
)

OWNER, TESTER = "owner@example.test", "tester@example.test"


def test_the_nightly_fetch_is_off_until_switched_on(database):
    before = database.nightly_enabled()
    at = database.set_nightly(True)
    on = database.nightly_enabled()
    database.set_nightly(False)

    assert before is False  # a new database fetches nothing
    assert on is True and database.nightly_switched_at() >= at
    assert database.nightly_enabled() is False


def test_a_second_nightly_run_finds_the_lock_taken(database):
    """Cloud Scheduler delivered one start twice, 30 s apart (2026-09-29): the
    second run must see the first and not start. A lock goes with its run."""
    with database.nightly_lock() as first:
        with database.nightly_lock() as second:
            assert (first, second) == (True, False)
    with database.nightly_lock() as next_night:
        assert next_night is True


def test_the_lock_keeps_its_connection_from_going_quiet(database):
    """Neon cut a connection that sat idle for 7 minutes (2026-09-30), and a
    lock goes with its connection: the fetch would lose it halfway. While it
    is held, its connection is asked something every `keepalive` seconds."""
    with database.nightly_lock(keepalive=0.05) as mine:
        time.sleep(0.4)
        with database.connect() as conn:
            quiet = conn.execute(
                "SELECT extract(epoch FROM clock_timestamp() - state_change) AS s "
                f"FROM pg_stat_activity WHERE pid = ({HOLDER})",
                (NIGHTLY_LOCK,),
            ).fetchone()["s"]

    assert mine is True
    assert quiet < 0.3  # busy a moment ago, not idle since the lock was taken


def test_a_lock_whose_connection_was_cut_ends_without_an_error(database):
    with database.nightly_lock(keepalive=0.05) as mine:
        with database.connect() as conn:
            conn.execute(f"SELECT pg_terminate_backend(({HOLDER}))", (NIGHTLY_LOCK,))
        time.sleep(0.2)
        held = database.nightly_running()

    assert mine is True and held is False  # the lock went with its connection
    with database.nightly_lock() as next_run:
        assert next_run is True


@pytest.fixture
def people(database):
    owner = database.create_user(email=OWNER, role="owner")
    tester = database.create_user(email=TESTER)
    return owner, tester


def test_only_the_owner_sees_and_flips_the_switch(database, people, tmp_path):
    with app_for(database, tmp_path, as_=TESTER) as http:
        seen = http.get("/api/admin/nightly")
        flipped = http.put("/api/admin/nightly", json={"enabled": True})
    with app_for(database, tmp_path, as_=OWNER) as http:
        first = http.get("/api/admin/nightly").json()
        on = http.put("/api/admin/nightly", json={"enabled": True}).json()

    assert seen.status_code == 403 and flipped.status_code == 403
    assert database.nightly_enabled() is True  # the owner's flip, not the tester's
    assert first == {
        "enabled": False,
        "switched_at": None,
        "published_at": None,
        "vacancies": None,
    }
    assert on["enabled"] is True and on["switched_at"] is not None


def test_the_card_shows_when_the_last_update_arrived(database, people, tmp_path):
    corpus, vectors = corpus_and_vectors(tmp_path)
    done = publish(database, corpus, vectors, MODEL)
    with app_for(database, tmp_path, as_=OWNER) as http:
        state = http.get("/api/admin/nightly").json()

    assert state["vacancies"] == 3
    assert state["published_at"].startswith(done.at.date().isoformat())
