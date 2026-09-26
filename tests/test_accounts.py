"""Signing in, keeping people apart, and leaving (7.5).

Against the test database, through the real app in-process. Skipped without
the Docker database.
"""

import json
from datetime import timedelta

import pytest
from conftest import FakeClient
from fastapi.testclient import TestClient
from test_api import SANNE, app_for, config_for, sign_in, upload
from test_cv_match import PROFILE
from test_service_matching import JUDGEMENT, WISHLIST

from joblens.api import create_app

LISA, SANNE_EMAIL = "lisa@example.test", "sanne@example.test"


@pytest.fixture
def people(database):
    return database.create_user(email=LISA), database.create_user(email=SANNE_EMAIL)


# -- a login link --------------------------------------------------------------


def test_opening_a_login_link_shows_a_button_and_signs_nobody_in(
    database, people, tmp_path
):
    with app_for(database, tmp_path, as_=None) as http:
        page = http.get("/login")

        assert page.status_code == 200
        assert 'id="go"' in page.text  # the button; its script signs in
        assert "script-src 'self'" in page.headers["Content-Security-Policy"]
        assert page.headers["Referrer-Policy"] == "no-referrer"
        assert "joblens_session" not in page.headers.get("set-cookie", "")
        assert http.get("/api/me").status_code == 401


def test_a_login_link_works_once(database, people, tmp_path):
    token = database.create_login_link(people[0].id)
    with app_for(database, tmp_path, as_=None) as http:
        first = http.post("/api/login", json={"token": token})
        again = http.post("/api/login", json={"token": token})

        assert first.status_code == 200 and first.json()["email"] == LISA
        cookie = first.headers["set-cookie"].lower()
        assert "httponly" in cookie and "samesite=lax" in cookie
        assert again.status_code == 401
        assert "used already" in again.json()["detail"]


def test_an_expired_link_or_session_opens_nothing(database, people, tmp_path):
    stale = database.create_login_link(people[0].id, valid_for=timedelta(seconds=-1))
    with app_for(database, tmp_path, as_=None) as http:
        assert http.post("/api/login", json={"token": stale}).status_code == 401

        sign_in(http, database, LISA)
        with database.connect() as conn:
            conn.execute("UPDATE sessions SET expires_at = now() - interval '1 s'")
        assert http.get("/api/me").status_code == 401


def test_signing_out_ends_the_session_everywhere(database, people, tmp_path):
    with app_for(database, tmp_path) as http:
        session = http.cookies["joblens_session"]

        assert http.post("/api/logout").status_code == 204

    # The cookie a browser might still hold opens nothing any more.
    assert database.session_user(session) is None


def test_the_database_never_holds_a_working_link_or_session(database, people, tmp_path):
    token = database.create_login_link(people[0].id)
    with app_for(database, tmp_path, as_=None) as http:
        http.post("/api/login", json={"token": token})
        session = http.cookies["joblens_session"]

    with database.connect() as conn:
        kept = {
            row["token_hash"]
            for table in ("login_links", "sessions")
            for row in conn.execute(f"SELECT token_hash FROM {table}").fetchall()
        }
    assert token not in kept and session not in kept
    assert len(kept) == 2  # their hashes are what is kept


def test_a_cookie_is_secure_unless_the_server_is_this_machine(database, tmp_path):
    with pytest.raises(ValueError, match="must be Secure"):
        create_app(config_for(database, tmp_path, allowed_hosts=("joblens.example",)))

    database.create_user(email=LISA)
    token = database.create_login_link(database.user_by_email(LISA).id)
    config = config_for(database, tmp_path, secure_cookies=True)
    with TestClient(create_app(config), headers={"X-JobLens": "1"}) as http:
        answer = http.post("/api/login", json={"token": token})
    assert "secure" in answer.headers["set-cookie"].lower()


# -- keeping people apart -------------------------------------------------------


def test_one_tester_cannot_reach_anothers_cv_run_or_match(database, people, tmp_path):
    client = FakeClient(json.dumps(PROFILE), WISHLIST, JUDGEMENT, JUDGEMENT, JUDGEMENT)
    with app_for(database, tmp_path, lambda s: client) as lisa:
        cv = upload(lisa).json()["id"]
        job = lisa.post("/api/matches").json()
        run = job["run_id"]
        assert job["status"] == "done"

    with app_for(database, tmp_path, as_=SANNE_EMAIL) as sanne:
        assert sanne.get("/api/me").json()["email"] == SANNE_EMAIL
        assert sanne.get("/api/cvs").json() == []
        assert sanne.get("/api/runs").json() == []
        assert sanne.get("/api/matches").json() == []
        # Knowing Lisa's ids is not enough.
        assert sanne.get(f"/api/runs/{run}").status_code == 404
        assert sanne.get(f"/api/matches/{job['id']}").status_code == 404
        assert sanne.post(f"/api/cvs/{cv}/activate").status_code == 404
        assert sanne.post("/api/matches").status_code == 409  # she has no CV


# -- leaving ------------------------------------------------------------------


def test_download_my_data_holds_everything_kept(database, people, tmp_path):
    client = FakeClient(json.dumps(PROFILE))
    with app_for(database, tmp_path, lambda s: client) as http:
        upload(http, strip_name="Sanne Vermeulen")
        http.put("/api/preferences", json={"salary_min": 3500})

        answer = http.get("/api/me/export")

    assert "attachment" in answer.headers["Content-Disposition"]
    everything = answer.json()
    assert everything["user"]["email"] == LISA
    assert everything["preferences"]["salary_min"] == 3500
    assert everything["cvs"][0]["filename"] == SANNE.name
    assert "Verpleegkundige" in everything["cvs"][0]["text"]


def test_delete_my_data_asks_for_words_and_then_removes_everything(
    database, people, tmp_path
):
    client = FakeClient(json.dumps(PROFILE))
    lisa = people[0]
    with app_for(database, tmp_path, lambda s: client) as http:
        upload(http)
        http.put("/api/preferences", json={"salary_min": 3500})

        wrong = http.post("/api/me/delete", json={"confirm": "yes"})
        gone = http.post("/api/me/delete", json={"confirm": "delete everything"})

        assert wrong.status_code == 400
        assert gone.json() == {"deleted": True}
        assert http.get("/api/me").status_code == 401

    assert database.user_by_email(LISA) is None
    with database.connect() as conn:
        for table in ("cvs", "preferences", "sessions", "login_links", "jobs"):
            count = conn.execute(
                f"SELECT count(*) AS n FROM {table} WHERE user_id = %s", (lisa.id,)
            ).fetchone()["n"]
            assert count == 0, table
    assert database.user_by_email(SANNE_EMAIL) is not None  # nobody else


def test_an_owner_is_an_owner(database):
    owner = database.create_user(email="mahdi@example.test", role="owner")

    assert owner.is_owner and not database.create_user().is_owner
