"""Starting and stopping the nightly job from the admin page (7.10.2): only the
owner, one run at a time, the arguments the job is given, and the calls Cloud
Run is sent -- against a fake job and a mocked Cloud Run, never the real one."""

import json

import httpx
import nightly
import pytest
from test_api import app_for

from joblens.cloud.jobs import NightlyJob
from joblens.sources.overview import SOURCES

OWNER, TESTER = "owner@example.test", "tester@example.test"
JOB = "projects/p-1/locations/europe-west4/jobs/joblens-nightly"


class FakeJob:
    def __init__(self, refuse: Exception | None = None):
        self.started: list[list[str]] = []
        self.stopped: list[str] = []
        self.refuse = refuse

    def start(self, args: list[str]) -> None:
        if self.refuse:
            raise self.refuse
        self.started.append(args)

    def stop(self, execution: str) -> None:
        if self.refuse:
            raise self.refuse
        self.stopped.append(execution)


@pytest.fixture
def people(database):
    return (
        database.create_user(email=OWNER, role="owner"),
        database.create_user(email=TESTER),
    )


@pytest.mark.parametrize(
    ("ask", "args"),
    [
        ({}, ["--force", "--by", "page"]),
        ({"fetch": False}, ["--force", "--by", "page", "--no-fetch"]),
        ({"source": "indeed"}, ["--force", "--by", "page", "--source", "indeed"]),
        ({"source": "linkedin"}, ["--force", "--by", "page", "--source", "linkedin"]),
    ],
)
def test_the_owner_starts_a_run_with_the_right_arguments(
    database, people, tmp_path, ask, args
):
    job = FakeJob()
    with app_for(database, tmp_path, as_=OWNER, nightly_job=job) as http:
        before = http.get("/api/admin/runs").json()
        started = http.post("/api/admin/runs", json=ask)

    assert before["can_start"] is True and before["starting"] is None
    assert started.status_code == 202
    assert job.started == [args]  # --force: the switch is for Cloud Scheduler
    assert started.json()["starting"] is not None


def test_the_page_is_told_every_source_one_run_may_fetch(database, people, tmp_path):
    """7.10.5: the page's "What to fetch" offers these, LinkedIn included, even
    before a run has counted it."""
    with app_for(database, tmp_path, as_=OWNER, nightly_job=FakeJob()) as http:
        state = http.get("/api/admin/runs").json()

    assert state["sources"] == list(SOURCES)
    assert "linkedin" in state["sources"]


def test_a_tester_can_neither_start_nor_stop(database, people, tmp_path):
    job = FakeJob()
    with app_for(database, tmp_path, as_=TESTER, nightly_job=job) as http:
        start = http.post("/api/admin/runs", json={})
        stop = http.post("/api/admin/runs/stop")

    assert start.status_code == 403 and stop.status_code == 403
    assert job.started == [] and job.stopped == []


@pytest.mark.parametrize(
    "ask",
    [{"source": "monster"}, {"fetch": False, "source": "indeed"}, {"force": True}],
)
def test_a_run_that_makes_no_sense_is_refused(database, people, tmp_path, ask):
    job = FakeJob()
    with app_for(database, tmp_path, as_=OWNER, nightly_job=job) as http:
        answer = http.post("/api/admin/runs", json=ask)

    assert answer.status_code == 422 and job.started == []


def test_without_a_job_the_page_says_it_cannot_start_one(database, people, tmp_path):
    with app_for(database, tmp_path, as_=OWNER) as http:
        state = http.get("/api/admin/runs").json()
        start = http.post("/api/admin/runs", json={})

    assert state["can_start"] is False
    assert start.status_code == 503


def test_one_run_at_a_time_starting_or_going(database, people, tmp_path):
    job = FakeJob()
    with app_for(database, tmp_path, as_=OWNER, nightly_job=job) as http:
        http.post("/api/admin/runs", json={})
        while_starting = http.post("/api/admin/runs", json={})
        # The job begins: it writes its row, and "starting" is over.
        database.start_nightly("page", fetch=True, execution="joblens-nightly-ab12c")
        begun = http.get("/api/admin/runs").json()
        with database.nightly_lock():
            while_going = http.post("/api/admin/runs", json={})

    assert while_starting.status_code == 409
    assert begun["starting"] is None
    assert begun["runs"][0]["trigger"] == "page"
    assert while_going.status_code == 409
    assert len(job.started) == 1


def test_stop_cancels_the_execution_that_is_going(database, people, tmp_path):
    job = FakeJob()
    database.start_nightly("schedule", fetch=True, execution="joblens-nightly-ab12c")
    with app_for(database, tmp_path, as_=OWNER, nightly_job=job) as http:
        nothing_going = http.post("/api/admin/runs/stop")
        with database.nightly_lock():
            stopped = http.post("/api/admin/runs/stop")

    assert nothing_going.status_code == 409  # the row is there, the lock is not
    assert stopped.status_code == 202
    assert job.stopped == ["joblens-nightly-ab12c"]


def test_cloud_runs_refusal_reaches_the_owner(database, people, tmp_path):
    answer = httpx.Response(
        403,
        json={"error": {"message": "Permission 'run.jobs.runWithOverrides' denied"}},
        request=httpx.Request("POST", "https://run.googleapis.com/v2/x:run"),
    )
    refused = httpx.HTTPStatusError("403", request=answer.request, response=answer)
    job = FakeJob(refuse=refused)
    with app_for(database, tmp_path, as_=OWNER, nightly_job=job) as http:
        start = http.post("/api/admin/runs", json={})
        state = http.get("/api/admin/runs").json()

    assert start.status_code == 502
    assert "403" in start.json()["detail"]
    assert "runWithOverrides" in start.json()["detail"]
    assert state["starting"] is None  # nothing was started, nothing is waited for


# -- what Cloud Run is sent -------------------------------------------------------


def cloud_run(seen: list):
    def answer(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"name": "operations/1"})

    return httpx.Client(transport=httpx.MockTransport(answer))


def test_start_and_stop_call_cloud_run_with_the_service_accounts_token():
    seen: list[httpx.Request] = []
    job = NightlyJob(JOB, token=lambda: "t0ken", client=cloud_run(seen))

    job.start(["--force", "--by", "page"])
    job.stop("joblens-nightly-ab12c")

    run, cancel = seen
    assert run.method == "POST"
    assert str(run.url) == f"https://run.googleapis.com/v2/{JOB}:run"
    assert json.loads(run.content) == {
        "overrides": {"containerOverrides": [{"args": ["--force", "--by", "page"]}]}
    }
    assert run.headers["Authorization"] == "Bearer t0ken"
    assert str(cancel.url) == (
        f"https://run.googleapis.com/v2/{JOB}/executions/joblens-nightly-ab12c:cancel"
    )


def test_names_that_are_not_names_are_refused():
    with pytest.raises(ValueError):
        NightlyJob("projects/p/jobs/../x", token=str, client=httpx.Client())
    job = NightlyJob(JOB, token=str, client=httpx.Client())
    with pytest.raises(ValueError):
        job.stop("../../other-job")


def test_without_a_job_name_there_is_no_job():
    assert NightlyJob.from_env({}) is None
    assert NightlyJob.from_env({"JOBLENS_NIGHTLY_JOB": JOB}).name == JOB


# -- what the job makes of its arguments ---------------------------------------------


@pytest.mark.parametrize(
    ("argv", "trigger", "fetch", "source"),
    [
        ([], "schedule", True, None),  # Cloud Scheduler
        (["--force"], "hand", True, None),  # gcloud run jobs execute --args=--force
        (["--force", "--no-fetch"], "hand", False, None),
        (["--force", "--by", "page", "--source", "indeed"], "page", True, "indeed"),
    ],
)
def test_the_job_knows_who_started_it_and_what_to_fetch(argv, trigger, fetch, source):
    args = nightly.options(argv)

    assert (args.trigger, args.fetch, args.source) == (trigger, fetch, source)
