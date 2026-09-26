"""The matches page (7.7 step 3): what a match found, and marking it with a reason.

What the page is sent (GET /api/results), what it can say about a vacancy
the person's answers moved, and the rules a mark keeps: a reason, kept as
typed, with what the judge said at the time, appended and never overwritten.
"""

import json

import pytest
from conftest import FakeClient, details
from test_api import EMAIL, app_for, upload
from test_cv_match import PROFILE
from test_service_matching import CARE, CORPUS, JUDGEMENT, WISHLIST

from joblens.api.views import moved
from joblens.corpus import Corpus
from joblens.preferences import Geo, Preferences
from joblens.preferences.rerank import conflicts
from joblens.preferences.schema import Conflict
from joblens.sources.base import Vacancy

# -- what a vacancy contradicts, in parts a page can translate --------------------


def test_every_kind_of_conflict_comes_apart_into_values_and_numbers():
    """Built by rerank's own code, so a change to its wording fails here
    rather than turning into English in a Dutch page."""
    geo = Geo.load()
    wants = Preferences(
        contract_types=["permanent"],
        work_modes=["remote"],
        hours_min=32,
        salary_min=4000,
        home="Utrecht",
        max_distance_km=40,
        languages=["Dutch"],
        seniority=["junior"],
        avoid_employers=["Coolblue"],
    )
    job = Vacancy(
        source="indeed",
        source_id="9",
        url="https://example.test",
        title="Senior developer",
        company="Coolblue",
        text="Senior developer",
    )
    stated = details(
        "Senior developer",
        contract_type="temporary",
        work_mode="onsite",
        hours_min=16,
        hours_max=24,
        salary_max=3200.0,
        salary_period="month",
        city="Groningen",
        languages_required=["German", "French"],
        company="Coolblue",
    )

    found = {
        one.field: moved(one)
        for one in conflicts(job, stated, wants, geo, geo.locate("Utrecht"))
    }

    assert set(found) == {
        "contract",
        "work_mode",
        "hours",
        "salary",
        "distance",
        "language",
        "seniority",
        "employer",
    }
    assert found["contract"].values == ["temporary"]
    assert found["work_mode"].values == ["onsite"]
    assert (found["hours"].low, found["hours"].high) == (16, 24)
    assert found["salary"].low == 3200
    assert found["distance"].place == "Utrecht" and found["distance"].low > 100
    assert found["language"].values == ["German", "French"]
    assert found["seniority"].values == ["senior"]
    assert found["employer"].values == ["Coolblue"]


def test_a_conflict_it_cannot_read_keeps_its_english_line():
    odd = moved(Conflict(field="salary", found="a lot less", wanted="more"))

    assert odd.low is None and odd.values == []
    assert odd.text == "salary: a lot less -- you want more"


# -- what the page is sent ---------------------------------------------------------


@pytest.fixture
def lisa(database):
    return database.create_user(email=EMAIL, display_name="Lisa")


def answer(verdict: str, fit: int) -> str:
    judged = json.loads(JUDGEMENT)
    judged.update(verdict=verdict, fit=fit, summary=f"Judged {verdict}.")
    judged["evidence"] = [{"requirement": "zorg", "cv_quote": "Verpleegkundige"}]
    return json.dumps(judged)


def a_match(http, top: int = 10) -> dict:
    """Upload Sanne's CV, run one match, and return what the page is sent."""
    upload(http)
    assert http.post("/api/matches", json={"top": top}).status_code == 202
    return http.get("/api/results").json()


def test_the_page_shows_every_judged_vacancy_strongest_first(database, lisa, tmp_path):
    replies = (answer("weak", 30), answer("strong", 82), answer("possible", 61))
    client = FakeClient(json.dumps(PROFILE), WISHLIST, *replies)
    with app_for(database, tmp_path, chat=lambda s: client) as http:
        before = http.get("/api/results")
        found = a_match(http)

    assert before.status_code == 404  # no CV, so nothing to show
    assert [one["verdict"] for one in found["matches"]] == [
        "strong",
        "possible",
        "weak",
    ]
    assert (found["strong"], found["possible"], found["weak"]) == (1, 1, 1)
    first = found["matches"][0]
    assert first["claims"] == [{"requirement": "zorg", "quote": "Verpleegkundige"}]
    assert first["summary"] == "Judged strong." and first["mark"] is None
    assert first["rank"] is not None and first["before"] is None  # no answers given
    assert found["with_preferences"] is False and found["pushed_out"] == []
    assert [one["id"] for one in found["runs"]] == [found["id"]]


def test_answers_show_what_they_moved_out_of_the_shortlist_and_why(
    database, lisa, tmp_path
):
    """Sanne is a nurse, so Altrecht's two care jobs rank first; avoiding
    Altrecht with a shortlist of one leaves Coolblue read and the first care
    job moved out -- listed, never hidden."""
    client = FakeClient(json.dumps(PROFILE), WISHLIST, answer("weak", 20))
    with app_for(database, tmp_path, chat=lambda s: client) as http:
        http.put("/api/preferences", json={"avoid_employers": ["Altrecht"]})
        found = a_match(http, top=1)
        board = http.get("/api/dashboard").json()
        out = found["pushed_out"][0]
        marked = http.post(
            "/api/marks",
            json={
                "run": found["id"],
                "key": out["key"],
                "call": "apply",
                "reason": "Altrecht is fine by me after all",
            },
        )

    assert [one["company"] for one in found["matches"]] == ["Coolblue"]
    assert len(found["pushed_out"]) == 1 and out["company"] == "Altrecht"
    assert out["before"] == 1 and out["rank"] > 1
    assert out["moved"][0]["field"] == "employer"
    assert out["moved"][0]["values"] == ["Altrecht"]
    assert found["matches"][0]["moved"] == []  # Coolblue contradicts nothing
    assert board["latest"]["moved"] == 2  # both care jobs moved back...
    assert board["latest"]["pushed_out"] == 1  # ...one of them out of the list
    # Never read, so there is no verdict to keep; the position is kept.
    assert marked.status_code == 200
    assert marked.json()["verdict"] == "" and marked.json()["rank"] == out["rank"]


def test_an_earlier_match_can_be_shown_but_not_another_cvs(database, lisa, tmp_path):
    from test_storage import record

    replies = [answer("strong", 80)] * 6
    client = FakeClient(json.dumps(PROFILE), WISHLIST, *replies)
    with app_for(database, tmp_path, chat=lambda s: client) as http:
        first = a_match(http)
        http.post("/api/matches", json={"top": 10})
        newest = http.get("/api/results").json()
        earlier = http.get(f"/api/results/{first['id']}")
        other = database.store_for(lisa.id).save_run(record(cv="somebody_else"))
        foreign = http.get(f"/api/results/{other}")

    assert [one["id"] for one in newest["runs"]][1] == first["id"]  # newest first
    assert newest["id"] != first["id"]
    assert earlier.status_code == 200 and earlier.json()["id"] == first["id"]
    assert foreign.status_code == 404  # an imported run of another CV stays out


# -- marking -------------------------------------------------------------------------


def test_a_mark_needs_a_reason_and_keeps_what_the_judge_said(database, lisa, tmp_path):
    replies = [answer("strong", 80)] * 3
    client = FakeClient(json.dumps(PROFILE), WISHLIST, *replies)
    with app_for(database, tmp_path, chat=lambda s: client) as http:
        found = a_match(http)
        run, key = found["id"], found["matches"][0]["key"]

        def mark(**change):
            given = {"run": run, "key": key, "call": "maybe", "reason": "Te ver"}
            return http.post("/api/marks", json=given | change)

        blank = mark(reason="   ")
        endless = mark(reason="x" * 1001)
        unknown_call = mark(call="love")
        not_in_run = mark(key="indeed:nope")
        no_run = mark(run="2020-01-01_0000_nobody")
        first = mark(reason="  Te ver weg, maar interessant ")
        second = mark(call="apply", reason="Toch doen: het team is goed")
        shown = http.get("/api/results").json()
        cv = http.get("/api/cvs/active").json()["name"]

    assert blank.status_code == 422 and "reason" in blank.json()["detail"]
    assert endless.status_code == 422
    assert unknown_call.status_code == 422
    assert not_in_run.status_code == 404 and no_run.status_code == 404
    saved = first.json()
    assert saved["reason"] == "Te ver weg, maar interessant"  # as typed, trimmed
    assert (saved["verdict"], saved["fit"], saved["run"]) == ("strong", 80, run)
    assert saved["rank"] == found["matches"][0]["rank"]
    assert second.status_code == 200
    assert shown["matches"][0]["mark"]["call"] == "apply"  # the newest shows
    labels = database.store_for(lisa.id).load_labels(cv)
    assert [one.call for one in labels.decisions] == ["maybe", "apply"]  # both kept
    assert labels.judged_by == "Lisa" and key in labels.relevant


def test_a_vacancy_that_closed_since_the_match_can_still_be_marked(
    database, lisa, tmp_path
):
    replies = [answer("strong", 80)] * 3
    client = FakeClient(json.dumps(PROFILE), WISHLIST, *replies)
    with app_for(database, tmp_path, chat=lambda s: client) as http:
        found = a_match(http)
    others = [one for one in CORPUS.vacancies if one.key != CARE.key]
    closed = Corpus("samples", others, {})
    with app_for(database, tmp_path, corpus=closed) as http:
        marked = http.post(
            "/api/marks",
            json={
                "run": found["id"],
                "key": CARE.key,
                "call": "no",
                "reason": "Nachtdiensten",
            },
        )

    assert marked.status_code == 200 and marked.json()["call"] == "no"


def test_nobody_reads_or_marks_another_persons_match(database, lisa, tmp_path):
    client = FakeClient(json.dumps(PROFILE), WISHLIST, *[answer("strong", 80)] * 3)
    with app_for(database, tmp_path, chat=lambda s: client) as http:
        found = a_match(http)
        cv = http.get("/api/cvs/active").json()["name"]
    database.create_user(email="sam@example.test")
    with app_for(database, tmp_path, as_="sam@example.test") as http:
        read = http.get(f"/api/results/{found['id']}")
        marked = http.post(
            "/api/marks",
            json={
                "run": found["id"],
                "key": found["matches"][0]["key"],
                "call": "no",
                "reason": "Niet van mij",
            },
        )

    assert read.status_code == 404 and marked.status_code == 404
    assert database.store_for(lisa.id).load_labels(cv) is None
