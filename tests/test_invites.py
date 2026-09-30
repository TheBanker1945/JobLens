"""Inviting people from the admin page (7.9.1): only the owner can, the link
signs the new person in once, a new link replaces an unused one, and the list
says who is in and whose link is still waiting."""

import pytest
from test_api import app_for

OWNER, NEW = "owner@example.test", "Noor@Example.test"


@pytest.fixture
def owner(database):
    return database.create_user(email=OWNER, role="owner")


def token_of(invited: dict) -> str:
    assert invited["link"].startswith("/login#")
    return invited["link"].removeprefix("/login#")


def test_only_the_owner_can_invite_or_see_who_is_invited(database, owner, tmp_path):
    database.create_user(email="tester@example.test")
    with app_for(database, tmp_path, as_="tester@example.test") as http:
        listed = http.get("/api/admin/people")
        made = http.post("/api/admin/invites", json={"email": NEW})

    assert listed.status_code == 403 and made.status_code == 403
    assert database.user_by_email(NEW) is None


def test_an_invite_makes_a_tester_whose_link_signs_them_in_once(
    database, owner, tmp_path
):
    with app_for(database, tmp_path, as_=OWNER) as http:
        invited = http.post(
            "/api/admin/invites",
            json={"email": NEW, "display_name": "Noor", "locale": "nl"},
        )
    with app_for(database, tmp_path, as_=None) as http:
        first = http.post("/api/login", json={"token": token_of(invited.json())})
        again = http.post("/api/login", json={"token": token_of(invited.json())})

    body = invited.json()
    assert invited.status_code == 201 and body["new_account"] is True
    assert body["person"]["email"] == "noor@example.test"  # one spelling
    assert body["person"]["role"] == "tester"
    assert body["person"]["link_until"] is not None
    assert first.status_code == 200
    assert first.json() | {"id": None, "created_at": None} == {
        "id": None,
        "email": "noor@example.test",
        "display_name": "Noor",
        "locale": "nl",
        "role": "tester",
        "onboarded_at": None,
        "created_at": None,
    }
    assert again.status_code == 401


def test_a_new_link_replaces_the_one_not_yet_used(database, owner, tmp_path):
    with app_for(database, tmp_path, as_=OWNER) as http:
        first = http.post("/api/admin/invites", json={"email": NEW}).json()
        second = http.post(
            "/api/admin/invites", json={"email": NEW, "display_name": "Someone else"}
        ).json()
    with app_for(database, tmp_path, as_=None) as http:
        old = http.post("/api/login", json={"token": token_of(first)})
        new = http.post("/api/login", json={"token": token_of(second)})

    assert second["new_account"] is False
    assert second["person"]["id"] == first["person"]["id"]
    assert second["person"]["display_name"] is None  # an account keeps its name
    assert old.status_code == 401 and new.status_code == 200


def test_the_list_says_whose_link_waits_and_who_is_signed_in(database, owner, tmp_path):
    with app_for(database, tmp_path, as_=OWNER) as http:
        noor = http.post("/api/admin/invites", json={"email": NEW}).json()
        http.post("/api/admin/invites", json={"email": "sam@example.test"})
        with database.connect() as conn:  # Sam's link ran out
            conn.execute(
                "UPDATE login_links SET expires_at = now() - interval '1 s' "
                "WHERE user_id = %s",
                (database.user_by_email("sam@example.test").id,),
            )
        with app_for(database, tmp_path, as_=None) as other:
            other.post("/api/login", json={"token": token_of(noor)})
        people = {one["email"]: one for one in http.get("/api/admin/people").json()}

    assert list(people) == ["sam@example.test", "noor@example.test", OWNER]
    assert people["noor@example.test"]["signed_in_at"] is not None
    assert people["noor@example.test"]["link_until"] is None  # used
    assert people["sam@example.test"]["signed_in_at"] is None
    assert people["sam@example.test"]["link_until"] is None  # needs a new one
    assert people[OWNER]["role"] == "owner"


@pytest.mark.parametrize(
    "given",
    [
        {"email": "not an address"},
        {"email": "noor@example"},
        {"email": NEW, "locale": "it"},
        {"email": NEW, "display_name": ""},
        {"email": NEW, "role": "owner"},  # never from a page
    ],
)
def test_a_bad_invite_is_refused_and_makes_nothing(database, owner, tmp_path, given):
    with app_for(database, tmp_path, as_=OWNER) as http:
        answer = http.post("/api/admin/invites", json=given)

    assert answer.status_code == 422
    assert database.user_by_email(NEW) is None
