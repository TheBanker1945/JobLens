"""The privacy page and the promise it makes (7.8.3).

The page says an uploaded file is deleted after 30 days, and links and
sessions after theirs. On a hosted server that has to happen by itself, so
the app purges at start and then every `purge_every`; these tests hold both
the page and that promise.
"""

import json
from datetime import timedelta

import pytest
from conftest import FakeClient
from test_api import EMAIL, app_for, upload
from test_cv_match import PROFILE


@pytest.fixture
def lisa(database):
    return database.create_user(email=EMAIL, display_name="Lisa")


def test_the_privacy_page_is_for_everyone_in_their_language(database, lisa, tmp_path):
    with app_for(database, tmp_path, as_=None) as http:
        page = http.get("/privacy", headers={"Accept-Language": "de-DE,de;q=0.9"})

    assert page.status_code == 200 and '<html lang="de">' in page.text
    assert 'id="who"' in page.text and 'id="rights"' in page.text
    assert "script-src 'self'" in page.headers["Content-Security-Policy"]


def test_the_page_names_who_runs_this_joblens_and_nobody_when_unset(
    database, lisa, tmp_path
):
    named = {"operator": "Mahdi", "contact": "privacy@example.test"}
    with app_for(database, tmp_path, as_=None, **named) as http:
        given = http.get("/api/privacy")
    with app_for(database, tmp_path, as_=None) as http:
        unset = http.get("/api/privacy").json()

    assert given.status_code == 200 and given.json() == named  # no session needed
    assert given.headers["Cache-Control"] == "no-store"
    assert unset == {"operator": None, "contact": None}


def test_a_hosted_server_deletes_expired_files_and_logins_by_itself(
    database, lisa, tmp_path
):
    client = FakeClient(json.dumps(PROFILE))
    with app_for(database, tmp_path, chat=lambda s: client) as http:
        upload(http)
    with database.connect() as conn:
        conn.execute("UPDATE cv_files SET expires_at = now() - interval '1 day'")
        conn.execute("UPDATE sessions SET expires_at = now() - interval '1 day'")

    with app_for(database, tmp_path, as_=None):  # no purging: nothing moves
        pass
    with database.connect() as conn:
        kept = conn.execute("SELECT count(*) AS n FROM cv_files").fetchone()["n"]
    with app_for(database, tmp_path, as_=None, purge_every=timedelta(hours=6)):
        pass  # starting is enough: the first purge runs before any request

    with database.connect() as conn:
        files = conn.execute("SELECT count(*) AS n FROM cv_files").fetchone()["n"]
        sessions = conn.execute("SELECT count(*) AS n FROM sessions").fetchone()["n"]
        cvs = conn.execute("SELECT count(*) AS n FROM cvs").fetchone()["n"]
    assert kept == 1
    assert (files, sessions) == (0, 0)
    assert cvs == 1  # the redacted text and profile stay; only the file went
