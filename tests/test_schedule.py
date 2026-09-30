"""The owner's schedule (7.10.3): weekdays and one or two hours, Dutch time;
the job started every hour runs only in a scheduled one, once, with the switch
on; and only the owner may change it."""

from datetime import UTC, datetime, timedelta

import nightly
import pytest
from pydantic import ValidationError
from test_api import app_for

from joblens.api.app import UI
from joblens.cloud.schedule import MIN_GAP, TZ, Schedule

OWNER, TESTER = "owner@example.test", "tester@example.test"


def dutch(*args) -> datetime:
    return datetime(*args, tzinfo=TZ)


def test_the_default_is_what_cloud_scheduler_did_until_now():
    every_night = Schedule()

    assert every_night.days == [0, 1, 2, 3, 4, 5, 6] and every_night.hours == [3]
    assert every_night.due(dutch(2026, 10, 1, 3, 0, 40))
    assert not every_night.due(dutch(2026, 10, 1, 4, 0, 5))


@pytest.mark.parametrize(
    "given",
    [
        {"days": []},
        {"hours": []},
        {"hours": [3, 12, 20]},  # at most two a day
        {"hours": [24]},
        {"days": [7]},
        {"hours": [3, 7]},  # four hours apart
        {"hours": [22, 2]},  # four hours apart, round midnight
    ],
)
def test_a_schedule_that_makes_no_sense_is_refused(given):
    with pytest.raises(ValidationError):
        Schedule(**given)


def test_the_page_checks_the_same_gap_the_server_decides():
    """The form says it sooner, in the owner's language; the server decides."""
    script = (UI / "assets" / "admin.js").read_text("utf-8")

    assert f"const MIN_GAP = {MIN_GAP};" in script


def test_days_and_hours_are_kept_sorted_and_once():
    schedule = Schedule(days=[4, 0, 0], hours=[15, 3])

    assert schedule.days == [0, 4] and schedule.hours == [3, 15]


def test_the_hour_is_dutch_time_summer_and_winter():
    at_three = Schedule(hours=[3])

    assert at_three.due(datetime(2026, 7, 1, 1, 0, tzinfo=UTC))  # 03:00 CEST
    assert at_three.due(datetime(2026, 12, 1, 2, 0, tzinfo=UTC))  # 03:00 CET
    assert not at_three.due(datetime(2026, 12, 1, 1, 0, tzinfo=UTC))


def test_the_next_run_skips_days_off_and_crosses_a_change_of_clocks():
    weekdays = Schedule(days=[0, 1, 2, 3, 4], hours=[6, 18])
    friday_evening = dutch(2026, 10, 23, 19, 30)
    # Clocks go back on Sunday 25 October 2026: the Monday run is still at 06:00.
    assert weekdays.next_after(friday_evening) == dutch(2026, 10, 26, 6)
    assert weekdays.next_after(dutch(2026, 10, 26, 6, 0, 30)) == dutch(2026, 10, 26, 18)


# -- the job, started every hour ------------------------------------------------


def test_the_hourly_start_runs_only_when_switched_on_due_and_not_run_yet(database):
    now = datetime.now(UTC)
    database.set_nightly_schedule(Schedule(hours=[now.astimezone(TZ).hour]))
    hourly = nightly.options([])

    off = nightly.should_run(database, hourly, now)
    database.set_nightly(True)
    not_due = nightly.should_run(database, hourly, now + timedelta(hours=3))
    due = nightly.should_run(database, hourly, now)
    database.start_nightly("schedule", fetch=True)  # this hour's run began
    again = nightly.should_run(database, hourly, now)
    forced = nightly.should_run(database, nightly.options(["--force"]), now)

    assert (off, not_due, due) == (False, False, True)
    assert again is False  # Cloud Scheduler's second delivery of one start
    assert forced is True  # by hand or from the page: whatever the schedule says


def test_a_run_in_an_earlier_hour_does_not_count_for_this_one(database):
    now = datetime.now(UTC)
    database.set_nightly_schedule(Schedule(hours=[now.astimezone(TZ).hour]))
    database.set_nightly(True)
    earlier = database.start_nightly("schedule", fetch=True)
    with database.connect() as conn:
        conn.execute(
            "UPDATE nightly_runs SET started_at = now() - interval '2 hours' "
            "WHERE id = %s",
            (earlier,),
        )

    assert nightly.should_run(database, nightly.options([]), now) is True


# -- the page ---------------------------------------------------------------------


@pytest.fixture
def people(database):
    return (
        database.create_user(email=OWNER, role="owner"),
        database.create_user(email=TESTER),
    )


def test_only_the_owner_sees_and_sets_the_schedule(database, people, tmp_path):
    new = {"days": [0, 2, 4], "hours": [6, 18]}
    with app_for(database, tmp_path, as_=TESTER) as http:
        refused = http.put("/api/admin/schedule", json=new)
    with app_for(database, tmp_path, as_=OWNER) as http:
        before = http.get("/api/admin/nightly").json()
        saved = http.put("/api/admin/schedule", json=new)
        wrong = http.put("/api/admin/schedule", json={"days": [0], "hours": [6, 8]})
        after = http.get("/api/admin/nightly").json()
        on = http.put("/api/admin/nightly", json={"enabled": True}).json()

    assert refused.status_code == 403
    assert before["schedule"] == {"days": [0, 1, 2, 3, 4, 5, 6], "hours": [3]}
    assert saved.status_code == 200 and after["schedule"] == new
    assert wrong.status_code == 422
    assert database.nightly_schedule() == Schedule(**new)
    assert after["next_run"] is None  # switched off: nothing runs by itself
    next_run = datetime.fromisoformat(on["next_run"]).astimezone(TZ)
    assert next_run.weekday() in new["days"] and next_run.hour in new["hours"]
