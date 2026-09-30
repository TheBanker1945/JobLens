"""The owner's admin page (7.10.1): what each nightly run did, the sources as
the newest run left them, and only the owner may see any of it."""

from datetime import UTC, datetime, timedelta

import nightly
import psycopg
import pytest
from test_api import app_for
from test_overview import CONFIG, NOW, build

from joblens.sources.overview import overview
from joblens.sources.report import RunReport, SearchRun
from joblens.storage.postgres import NIGHTLY_LOCK

OWNER, TESTER = "owner@example.test", "tester@example.test"


@pytest.fixture
def people(database):
    return (
        database.create_user(email=OWNER, role="owner"),
        database.create_user(email=TESTER),
    )


def seen(tmp_path) -> dict:
    return overview(build(tmp_path / "root"), CONFIG, now=NOW).model_dump(mode="json")


def test_a_run_is_written_down_when_it_starts_and_filled_in_when_it_ends(
    database, tmp_path
):
    run_id = database.start_nightly("schedule", fetch=True)
    (going,) = database.nightly_runs()
    database.finish_nightly(
        run_id,
        steps={"fetch": False, "index": True, "publish": True},
        new_vacancies=12,
        problems=["indeed (3 searches): throttled"],
        overview=seen(tmp_path),
    )
    (done,) = database.nightly_runs()
    as_of, stored = database.nightly_overview()

    assert going.finished_at is None and going.steps == {}
    assert (going.trigger, going.fetched) == ("schedule", True)
    assert done.finished_at is not None and as_of == done.finished_at
    assert done.steps == {"fetch": False, "index": True, "publish": True}
    assert list(done.steps) == ["fetch", "index", "publish"]  # in the order run
    assert done.new_vacancies == 12
    assert done.problems == ["indeed (3 searches): throttled"]
    assert done.in_joblens == 3  # greenhouse 1, indeed 1, jobdataapi 1
    assert stored["sources"][0]["source"] == "greenhouse"


def test_the_page_sees_a_run_going_only_while_it_holds_the_lock(database):
    """A run that was cut off (a timeout, a crash) never finishes its row, and
    its lock went with its process: it must not read as going for ever."""
    database.start_nightly("hand", fetch=False)
    before = database.nightly_running()
    with database.nightly_lock() as mine:
        during = database.nightly_running()
    after = database.nightly_running()

    assert mine is True
    assert (before, during, after) == (False, True, False)


def test_a_lock_in_another_database_is_not_a_run_here(database):
    """pg_locks lists every database on the server. Found in the walk: a lock
    held in the walk's database made the test database look busy."""
    elsewhere = database.url.rsplit("/", 1)[0] + "/postgres"
    with psycopg.connect(elsewhere, autocommit=True) as conn:
        conn.execute("SELECT pg_advisory_lock(%s)", (NIGHTLY_LOCK,))
        seen = database.nightly_running()

    assert seen is False


def test_runs_older_than_90_days_go_when_a_new_one_starts(database):
    old = database.start_nightly("schedule", fetch=True)
    with database.connect() as conn:
        conn.execute(
            "UPDATE nightly_runs SET started_at = now() - interval '91 days' "
            "WHERE id = %s",
            (old,),
        )
    new = database.start_nightly("schedule", fetch=True)

    assert [run.id for run in database.nightly_runs()] == [new]


def test_the_newest_overview_is_shown_even_after_a_run_that_had_none(
    database, tmp_path
):
    """A run whose overview could not be read keeps the page on the last one."""
    first = database.start_nightly("schedule", fetch=True)
    database.finish_nightly(
        first, steps={}, new_vacancies=None, problems=[], overview=seen(tmp_path)
    )
    second = database.start_nightly("schedule", fetch=True)
    database.finish_nightly(
        second, steps={}, new_vacancies=None, problems=[], overview=None
    )

    as_of, _ = database.nightly_overview()
    assert as_of == database.nightly_runs()[1].finished_at  # the first run's


def test_only_the_owner_gets_the_page_and_its_data(database, people, tmp_path):
    with app_for(database, tmp_path, as_=None) as http:
        outside = http.get("/admin", follow_redirects=False)
    with app_for(database, tmp_path, as_=TESTER) as http:
        page = http.get("/admin", follow_redirects=False)
        sources = http.get("/api/admin/sources")
        runs = http.get("/api/admin/runs")
    with app_for(database, tmp_path, as_=OWNER) as http:
        mine = http.get("/admin", headers={"Accept-Language": "nl-NL"})

    assert outside.status_code == 303 and outside.headers["location"] == "/login"
    assert page.status_code == 303 and page.headers["location"] == "/"
    assert sources.status_code == 403 and runs.status_code == 403
    assert mine.status_code == 200 and '<html lang="nl">' in mine.text
    assert "script-src 'self'" in mine.headers["Content-Security-Policy"]


def test_the_owner_sees_the_sources_and_the_runs(database, people, tmp_path):
    with app_for(database, tmp_path, as_=OWNER) as http:
        before = http.get("/api/admin/sources").json()
        run_id = database.start_nightly("hand", fetch=True)
        going = http.get("/api/admin/runs").json()
        database.finish_nightly(
            run_id,
            steps={"fetch": True},
            new_vacancies=2,
            problems=[],
            overview=seen(tmp_path),
        )
        after = http.get("/api/admin/sources").json()
        runs = http.get("/api/admin/runs").json()

    assert before is None  # no run yet
    assert going["running"] is False and going["runs"][0]["finished_at"] is None
    assert after["as_of"] == runs["runs"][0]["finished_at"]
    greenhouse = after["overview"]["sources"][0]
    assert (greenhouse["source"], greenhouse["stored"], greenhouse["open"]) == (
        "greenhouse",
        4,
        2,
    )
    assert [row["name"] for row in greenhouse["rows"]] == [
        "adyen",
        "catawiki",
        "newboard",
        None,
    ]
    assert after["overview"]["refusing"][0]["site"] == "indeed.com"
    assert runs["runs"][0]["new_vacancies"] == 2


def test_the_nightly_job_keeps_only_its_own_fetch_report(
    database, tmp_path, monkeypatch
):
    """A --no-fetch run, or a fetch that broke before writing its report, must
    not take an earlier night's new vacancies and problems as its own."""
    root = build(tmp_path / "root")
    (root / "sources.toml").write_text("[indeed]\nenabled = true\n", encoding="utf-8")
    (root / "boards.toml").write_text("", encoding="utf-8")
    monkeypatch.setattr(nightly, "ROOT", root)
    since = datetime.now(UTC).replace(microsecond=0)

    stale = database.start_nightly("schedule", fetch=True)
    nightly.record(database, stale, {"fetch": False}, since)
    tonight = RunReport(started_at=since + timedelta(seconds=5))
    tonight.add(SearchRun("indeed", "python in Utrecht", listed=3, stored=3))
    tonight.write(root / "data" / "raw" / "runs")
    fresh = database.start_nightly("schedule", fetch=True)
    nightly.record(database, fresh, {"fetch": True, "index": True}, since)
    no_fetch = database.start_nightly("hand", fetch=False)
    nightly.record(database, no_fetch, {"index": True}, since)

    runs = {run.id: run for run in database.nightly_runs()}
    assert runs[stale].new_vacancies is None and runs[stale].problems == []
    assert runs[fresh].new_vacancies == 3
    assert runs[no_fetch].new_vacancies is None
    assert runs[fresh].in_joblens == 3  # the overview was read and stored
