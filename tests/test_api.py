"""The web API (7.4), run in-process against the test database.

The model and the embedder are fakes (test_service_matching.py), jobs run
before the request returns (InlineRunner), and nothing leaves the machine.
Skipped without the Docker database, like the other database tests.
"""

import json

import httpx
import openai
import pytest
from conftest import SAMPLE_CVS, FakeClient
from fastapi.testclient import TestClient
from test_cv_match import PROFILE
from test_service_matching import CORPUS, JUDGEMENT, MODELS, WISHLIST, CareOrData

from joblens.api import AppConfig, InlineRunner, create_app

EMAIL = "lisa@example.test"
SANNE = SAMPLE_CVS / "sanne_vermeulen.md"


class NeverRuns:
    """A runner that queues and never starts: a job stays open."""

    def submit(self, work, *args, **kwargs):
        pass

    def shutdown(self):
        pass


def app_for(database, tmp_path, chat, runner=None, **extra) -> TestClient:
    config = AppConfig(
        database=database,
        corpus=CORPUS,
        models=MODELS,
        cache_dir=tmp_path,
        dev_user=EMAIL,
        chat=chat,
        embed=CareOrData,
        runner=runner or InlineRunner(),
        allowed_hosts=("testserver",),
        **extra,
    )
    return TestClient(create_app(config), headers={"X-JobLens": "1"})


@pytest.fixture
def lisa(database):
    return database.create_user(email=EMAIL)


def upload(http: TestClient, path=SANNE, **form):
    return http.post(
        "/api/cvs",
        files={"file": (path.name, path.read_bytes(), "text/markdown")},
        data=form,
    )


def test_it_says_who_is_signed_in_and_how_much_it_holds(database, lisa, tmp_path):
    with app_for(database, tmp_path, lambda s: FakeClient()) as http:
        assert http.get("/api/health").json() == {"ok": True, "vacancies": 3}
        assert http.get("/api/me").json()["email"] == EMAIL


def test_a_changing_request_from_another_page_is_refused(database, lisa, tmp_path):
    with app_for(database, tmp_path, lambda s: FakeClient()) as http:
        # A form another site makes the browser send: no header of ours.
        refused = http.put(
            "/api/preferences", json={"salary_min": 1}, headers={"X-JobLens": ""}
        )
        assert refused.status_code == 403
        assert http.get("/api/preferences", headers={"X-JobLens": ""}).is_success


def test_a_request_for_another_host_is_refused(database, lisa, tmp_path):
    with app_for(database, tmp_path, lambda s: FakeClient()) as http:
        assert http.get("/api/me", headers={"Host": "evil.example"}).status_code == 400


def test_nobody_is_signed_in_without_an_account(database, tmp_path):
    with app_for(database, tmp_path, lambda s: FakeClient()) as http:
        assert http.get("/api/me").status_code == 401


def test_an_upload_is_kept_redacted_and_becomes_the_active_cv(database, lisa, tmp_path):
    client = FakeClient(json.dumps(PROFILE))
    with app_for(database, tmp_path, lambda s: client) as http:
        made = upload(http, strip_name="Sanne Vermeulen")

        assert made.status_code == 201
        cv = made.json()
        assert cv["active"] and cv["has_profile"]
        assert cv["removed"]["email"] == 1
        assert "sanne.vermeulen@example.com" not in cv["text"]
        assert "Sanne" not in cv["text"]  # the name went, as asked
        assert http.get("/api/cvs/active").json()["id"] == cv["id"]
        assert [one["id"] for one in http.get("/api/cvs").json()] == [cv["id"]]
        assert "text" not in http.get("/api/cvs").json()[0]  # a list is light


def test_a_file_that_is_not_a_cv_is_refused_as_the_files_fault(
    database, lisa, tmp_path
):
    with app_for(database, tmp_path, lambda s: FakeClient()) as http:
        refused = http.post(
            "/api/cvs", files={"file": ("setup.exe", b"MZ\x90\x00", "application/x")}
        )

        assert refused.status_code == 422
        assert "Cannot read .exe" in refused.json()["detail"]


def test_preferences_are_checked_before_they_are_kept(database, lisa, tmp_path):
    with app_for(database, tmp_path, lambda s: FakeClient()) as http:
        bad = http.put("/api/preferences", json={"hours_min": 40, "hours_max": 24})
        good = http.put(
            "/api/preferences",
            json={
                "contract_types": ["permanent"],
                "home": "Leiden",
                "max_distance_km": 30,
            },
        )

        assert bad.status_code == 422
        assert good.is_success
        assert http.get("/api/preferences").json()["home"] == "Leiden"


def test_there_is_nothing_to_match_without_a_cv(database, lisa, tmp_path):
    with app_for(database, tmp_path, lambda s: FakeClient()) as http:
        assert http.post("/api/matches").status_code == 409


def test_a_match_runs_as_a_job_and_leaves_a_run_behind(database, lisa, tmp_path):
    client = FakeClient(json.dumps(PROFILE), WISHLIST, JUDGEMENT)
    with app_for(database, tmp_path, lambda s: client) as http:
        upload(http)
        http.put("/api/preferences", json={"avoid_employers": ["Altrecht"]})

        started = http.post("/api/matches", json={"top": 1})

        assert started.status_code == 202
        job = http.get(f"/api/matches/{started.json()['id']}").json()
        assert (job["status"], job["stage"], job["done"], job["total"]) == (
            "done",
            "judging",
            1,
            1,
        )
        run = http.get(f"/api/runs/{job['run_id']}").json()
        judged = run["recommended"] + run["rejected"]
        # The preference moved both Altrecht jobs back: Coolblue was judged.
        assert [row["company"] for row in judged] == ["Coolblue"]
        assert run["stamp"]["preferences"].startswith("p1:")
        assert [one["id"] for one in http.get("/api/runs").json()] == [job["run_id"]]


def test_a_second_match_while_one_is_open_is_refused(database, lisa, tmp_path):
    client = FakeClient(json.dumps(PROFILE))
    with app_for(database, tmp_path, lambda s: client, runner=NeverRuns()) as http:
        upload(http)

        first = http.post("/api/matches")
        second = http.post("/api/matches")

        assert first.status_code == 202 and first.json()["status"] == "queued"
        assert second.status_code == 409
        assert "already running" in second.json()["detail"]


class Busy:
    def chat(self, *args, **kwargs):
        raise openai.APIStatusError(
            "high demand",
            response=httpx.Response(503, request=httpx.Request("POST", "http://x")),
            body=None,
        )

    def close(self):
        pass


def test_a_busy_provider_ends_the_job_with_a_sentence(database, lisa, tmp_path):
    clients = iter([FakeClient(json.dumps(PROFILE))])

    def chat(settings):
        return next(clients, Busy())  # the upload works; the match does not

    with app_for(database, tmp_path, chat) as http:
        upload(http)
        job = http.post("/api/matches").json()

        assert job["status"] == "failed"
        assert "try again in a few minutes" in job["error"]
        assert http.post("/api/matches").status_code == 202  # the slot is free


def test_a_restart_closes_the_jobs_it_cut_off(database, lisa, tmp_path):
    client = FakeClient(json.dumps(PROFILE))
    with app_for(database, tmp_path, lambda s: client, runner=NeverRuns()) as http:
        upload(http)
        job_id = http.post("/api/matches").json()["id"]

    with app_for(database, tmp_path, lambda s: FakeClient()) as http:
        job = http.get(f"/api/matches/{job_id}").json()

        assert job["status"] == "failed"
        assert "restarted" in job["error"]


def test_the_docs_page_can_send_the_header(database, lisa, tmp_path):
    """/api/docs offers "Authorize" for X-JobLens, or it could change nothing."""
    with app_for(database, tmp_path, lambda s: FakeClient()) as http:
        spec = http.get("/api/openapi.json").json()

    schemes = spec["components"]["securitySchemes"].values()
    assert {"type": "apiKey", "in": "header", "name": "X-JobLens"}.items() <= next(
        iter(schemes)
    ).items()
