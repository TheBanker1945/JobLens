"""What only the database does: migrations, accounts, CVs, and keeping people apart.

These run against the test database in Docker (conftest.py) and skip without
it. The promises every store keeps are in test_store_contract.py.
"""

from datetime import UTC, datetime, timedelta

import psycopg
import pytest
from test_cv_match import PROFILE
from test_storage import record

from joblens.cv.runs import digest
from joblens.cv.schema import CVProfile
from joblens.evals.matching import CVLabels
from joblens.storage import KEEP_ORIGINAL
from joblens.storage.migrate import MigrationError, migrate, migrations, status

# -- migrations ---------------------------------------------------------------


@pytest.fixture
def scratch(database):
    """A connection whose tables land in a throwaway schema, so the runner can
    be tested with made-up migrations without touching the real ones."""
    with database.connect() as conn:
        conn.execute("DROP SCHEMA IF EXISTS migration_test CASCADE")
        conn.execute("CREATE SCHEMA migration_test")
        conn.execute("SET search_path TO migration_test")
        yield conn
        conn.execute("DROP SCHEMA migration_test CASCADE")


def write(directory, name: str, sql: str):
    (directory / name).write_text(sql, encoding="utf-8")


def test_each_migration_is_applied_once_and_in_order(scratch, tmp_path):
    write(tmp_path, "0002_second.sql", "ALTER TABLE one ADD COLUMN b int;")
    write(tmp_path, "0001_first.sql", "CREATE TABLE one (a int);")

    first = migrate(scratch, tmp_path)
    again = migrate(scratch, tmp_path)

    assert [one.name for one in first] == ["0001_first.sql", "0002_second.sql"]
    assert again == []  # up to date: safe to run on every start
    assert all(one.applied_at for one in status(scratch, tmp_path))


def test_a_migration_that_fails_leaves_nothing_half_applied(scratch, tmp_path):
    write(tmp_path, "0001_first.sql", "CREATE TABLE one (a int);")
    write(tmp_path, "0002_broken.sql", "CREATE TABLE two (b int); SELECT nonsense;")

    with pytest.raises(psycopg.errors.UndefinedColumn):
        migrate(scratch, tmp_path)

    exists = scratch.execute("SELECT to_regclass('two') AS t").fetchone()
    assert exists["t"] is None  # 0002's first statement was rolled back with it
    assert [one.applied_at is not None for one in status(scratch, tmp_path)] == [
        True,
        False,
    ]


def test_an_applied_migration_that_was_edited_stops_everything(scratch, tmp_path):
    write(tmp_path, "0001_first.sql", "CREATE TABLE one (a int);")
    migrate(scratch, tmp_path)
    write(tmp_path, "0001_first.sql", "CREATE TABLE one (a int, sneaky int);")

    with pytest.raises(MigrationError, match="changed after it was applied"):
        migrate(scratch, tmp_path)


def test_code_older_than_its_database_stops(scratch, tmp_path):
    write(tmp_path, "0001_first.sql", "CREATE TABLE one (a int);")
    write(tmp_path, "0002_second.sql", "CREATE TABLE two (a int);")
    migrate(scratch, tmp_path)
    (tmp_path / "0002_second.sql").unlink()

    with pytest.raises(MigrationError, match="newer code"):
        migrate(scratch, tmp_path)


def test_two_files_with_one_number_are_refused(tmp_path):
    write(tmp_path, "0001_a.sql", "")
    write(tmp_path, "0001_b.sql", "")

    with pytest.raises(MigrationError, match="same number"):
        migrations(tmp_path)


# -- people -------------------------------------------------------------------


def test_an_email_is_one_account_however_it_is_capitalised(database):
    made = database.create_user(email=" Mahdi@Example.test ", locale="nl")

    assert database.user_by_email("mahdi@example.test").id == made.id
    assert database.user_by_email("MAHDI@EXAMPLE.TEST").id == made.id
    with pytest.raises(ValueError, match="already an account"):
        database.create_user(email="mahdi@example.test")


@pytest.mark.parametrize("bad", ["not-a-uuid", "00000000-0000-0000-0000-000000000000"])
def test_a_store_is_only_handed_out_for_a_real_person(database, bad):
    with pytest.raises(KeyError):
        database.store_for(bad)


# -- CVs ----------------------------------------------------------------------


def test_a_new_cv_is_the_active_one_and_the_old_one_becomes_history(database):
    store = database.store_for(database.create_user().id)
    first = store.add_cv("lisa_nl.pdf", "Data-analist bij Coolblue.")
    second = store.add_cv("lisa_en.pdf", "Data analyst at Coolblue.")

    assert store.active_cv().id == second.id
    assert [one.id for one in store.cvs()] == [second.id, first.id]
    assert [one.active for one in store.cvs()] == [True, False]

    store.activate_cv(first.id)  # going back to an older one

    assert store.active_cv().id == first.id
    assert sum(one.active for one in store.cvs()) == 1


def test_one_active_cv_is_a_rule_the_database_keeps(database):
    """Not a promise the code makes: a second active row is refused outright."""
    user = database.create_user()
    database.store_for(user.id).add_cv("cv.md", "tekst")

    with database.connect() as conn, pytest.raises(psycopg.errors.UniqueViolation):
        conn.execute(
            "INSERT INTO cvs (user_id, name, filename, text, digest, active) "
            "VALUES (%s, 'x', 'x.md', 'x', 'x', true)",
            (user.id,),
        )


def test_a_cv_keeps_its_text_profile_and_name_the_way_runs_expect(database):
    store = database.store_for(database.create_user().id)
    profile = CVProfile.model_validate(PROFILE)

    kept = store.add_cv(
        "../../uploads/sanne_vermeulen.md",
        "Verpleegkundige, Altrecht.",
        profile=profile,
        strip_name="Sanne Vermeulen",
    )

    loaded = store.load_cv(kept.id)
    assert loaded.name == "sanne_vermeulen"  # the file stem, and nothing above it
    assert loaded.filename == "sanne_vermeulen.md"
    assert loaded.digest == digest("Verpleegkundige, Altrecht.")  # as a run stamps
    assert loaded.profile == profile
    assert loaded.strip_name == "Sanne Vermeulen"


def test_the_uploaded_file_is_kept_for_thirty_days_and_then_purged(database):
    store = database.store_for(database.create_user().id)
    now = datetime.now(UTC)
    old = store.add_cv(
        "old.pdf", "tekst", original=b"%PDF old", at=now - KEEP_ORIGINAL - timedelta(1)
    )
    new = store.add_cv("new.pdf", "tekst", original=b"%PDF new", at=now)

    assert store.original(old.id) is None  # past its date: not handed out
    kept = store.original(new.id)
    assert (kept.name, kept.data) == ("new.pdf", b"%PDF new")

    assert database.purge_expired_files() == 1
    assert store.original(new.id) is not None
    assert store.load_cv(old.id).text == "tekst"  # the CV itself stays


# -- keeping people apart -----------------------------------------------------


def test_one_persons_store_cannot_reach_anothers_data(database):
    lisa = database.store_for(database.create_user(email="lisa@example.test").id)
    sanne = database.store_for(database.create_user(email="sanne@example.test").id)
    run_id = lisa.save_run(record(cv="lisa"))
    lisa.save_labels(CVLabels(cv="lisa", corpus="raw", judged_by="Lisa"))
    lisa.save_preferences({"regio": ["Utrecht"]})
    cv = lisa.add_cv("lisa.pdf", "tekst", original=b"%PDF")

    assert sanne.runs() == [] and sanne.labels() == [] and sanne.cvs() == []
    assert sanne.load_preferences() is None and sanne.active_cv() is None
    with pytest.raises(KeyError):
        sanne.load_run(run_id)  # knowing the id is not enough
    with pytest.raises(KeyError):
        sanne.load_cv(cv.id)
    with pytest.raises(KeyError):
        sanne.activate_cv(cv.id)
    assert sanne.original(cv.id) is None
    assert lisa.active_cv().id == cv.id  # and nothing of Lisa's moved


def test_two_people_can_have_runs_with_the_same_id(database):
    """Ids are per person: two people named Lisa running in the same minute."""
    one = database.store_for(database.create_user().id)
    two = database.store_for(database.create_user().id)

    assert one.save_run(record(cv="lisa")) == two.save_run(record(cv="lisa"))


def test_deleting_a_person_deletes_everything_they_stored(database):
    lisa_user = database.create_user(email="lisa@example.test")
    lisa = database.store_for(lisa_user.id)
    sanne = database.store_for(database.create_user(email="sanne@example.test").id)
    for store in (lisa, sanne):
        store.save_run(record())
        store.save_labels(CVLabels(cv="mahdi", corpus="raw", judged_by="x"))
        store.save_preferences({"regio": ["Utrecht"]})
        store.add_cv("cv.pdf", "tekst", original=b"%PDF")

    assert database.delete_user(lisa_user.id) is True

    with database.connect() as conn:
        for table in ("cvs", "runs", "labels", "preferences"):
            count = conn.execute(
                f"SELECT count(*) AS n FROM {table} WHERE user_id = %s",
                (lisa_user.id,),
            ).fetchone()["n"]
            assert count == 0, table
        orphans = conn.execute(
            "SELECT count(*) AS n FROM cv_files f "
            "LEFT JOIN cvs c ON c.id = f.cv_id WHERE c.id IS NULL"
        ).fetchone()["n"]
        assert orphans == 0
    assert database.user_by_email("lisa@example.test") is None
    assert len(sanne.runs()) == 1 and sanne.active_cv() is not None
    assert database.delete_user(lisa_user.id) is False  # nothing left to delete


# -- jobs (7.4) ---------------------------------------------------------------


def test_one_open_job_per_person_and_the_slot_frees_when_it_ends(database):
    user = database.create_user()
    store = database.store_for(user.id)
    job = store.start_job("match", {"top": 10})

    with pytest.raises(ValueError, match="already running"):
        store.start_job("match", {"top": 10})
    database.update_job(job.id, status="running", stage="judging", done=3, total=10)
    assert (store.job(job.id).stage, store.job(job.id).done) == ("judging", 3)

    database.update_job(job.id, status="done", run_id="2026-09-25_1636_x")
    again = store.start_job("match", {"top": 5})
    assert [one.id for one in store.jobs()] == [again.id, job.id]


def test_a_job_update_only_writes_job_fields(database):
    job = database.store_for(database.create_user().id).start_job("match", {})

    with pytest.raises(ValueError, match="not a job field"):
        database.update_job(job.id, user_id="someone else")


def test_another_persons_job_is_not_found(database):
    mine = database.store_for(database.create_user().id)
    theirs = database.store_for(database.create_user().id)
    job = theirs.start_job("match", {})

    with pytest.raises(KeyError):
        mine.job(job.id)


def test_what_redaction_removed_is_kept_as_counts(database):
    store = database.store_for(database.create_user().id)

    cv = store.add_cv("cv.md", "tekst", removed={"email": 1, "phone": 2})

    assert store.load_cv(cv.id).removed == {"email": 1, "phone": 2}
