"""The pages (7.7 step 1): language, the rules the files keep, and what they show.

The pages themselves are plain HTML, CSS and JavaScript (src/joblens/api/ui/);
what can be checked without a browser is checked here: which language a
request gets, that the dictionaries agree, that no page breaks its own
Content-Security-Policy or puts text in as HTML, and what the dashboard is sent.
"""

import json
import re

import pytest
from conftest import FakeClient
from test_api import EMAIL, SANNE, app_for, upload
from test_cv_match import PROFILE
from test_service_matching import JUDGEMENT, WISHLIST

from joblens.api import language
from joblens.api.app import UI

# -- which language ---------------------------------------------------------------


@pytest.mark.parametrize(
    ("header", "saved", "chosen"),
    [
        ("nl-NL,nl;q=0.9,en;q=0.8", None, "nl"),
        ("en-NL,en;q=0.9", None, "en"),  # set to English in NL: English
        ("pl-NL,pl;q=0.9", None, "nl"),  # a language we lack: the country's
        ("tr-DE", None, "en"),  # German is not offered yet: English
        ("fr-BE,fr;q=0.9", None, "nl"),  # French not offered yet; Belgium is nl
        ("de;q=0.3,nl;q=0.7", None, "nl"),  # the most wanted first
        ("nl-NL", "en", "en"),  # the switcher's choice wins
        ("nl-NL", "xx", "nl"),  # a saved code that is not offered is ignored
        ("*", None, "en"),
        ("", None, "en"),
        ("garbage;;q=x,,,-", None, "en"),
    ],
)
def test_a_page_is_in_the_language_the_browser_asks_for(header, saved, chosen):
    assert language.pick(header, saved, offered=("en", "nl")) == chosen


def test_only_languages_with_texts_are_offered():
    assert language.available() == ("en", "nl")  # de, fr, es arrive in step 4
    assert language.pick("de-DE", offered=("en", "nl", "de")) == "de"


# -- the rules the files keep -------------------------------------------------


def dictionary(code: str) -> dict:
    return json.loads((UI / "assets" / "i18n" / f"{code}.json").read_text("utf-8"))


def test_every_language_has_exactly_the_same_texts():
    english = dictionary("en")
    for code in language.available():
        assert dictionary(code).keys() == english.keys(), code
        placeholders = {
            key: set(re.findall(r"\{\w+\}", text)) for key, text in english.items()
        }
        for key, text in dictionary(code).items():
            assert set(re.findall(r"\{\w+\}", text)) == placeholders[key], (code, key)


def test_every_text_a_page_asks_for_exists():
    keys = set(dictionary("en"))
    asked = set()
    for page in UI.glob("*.html"):
        html = page.read_text("utf-8")
        asked |= set(re.findall(r'data-i18n="([^"]+)"', html))
        asked |= {
            pair.split(":")[1]
            for attr in re.findall(r'data-i18n-attr="([^"]+)"', html)
            for pair in attr.split(";")
        }
    # Not only t("..."): the form keeps keys in tables ("prefs.years.0") and
    # hands them over later, so every dotted string in a script counts. A
    # plural is asked for by its stem ("hero.strong" -> "hero.strong.other").
    for script in (UI / "assets").glob("*.js"):
        asked |= set(re.findall(r'"([a-z]+(?:\.\w+)+)"', script.read_text("utf-8")))
    missing = {key for key in asked - keys if f"{key}.other" not in keys}
    assert not missing, missing


def test_no_page_breaks_its_own_security_policy():
    """script-src 'self' and style-src 'self': inline code or style would be
    blocked by the browser, so none is written."""
    for page in UI.glob("*.html"):
        html = page.read_text("utf-8")
        assert " style=" not in html, page.name
        assert not re.search(r"\son[a-z]+=", html), page.name  # no onclick=...
        for tag in re.findall(r"<script[^>]*>", html):
            assert "src=" in tag, (page.name, tag)
        for inline in re.findall(r"<script[^>]*>(.*?)</script>", html, re.S):
            assert not inline.strip(), page.name


def test_no_script_puts_text_into_the_page_as_html():
    """Titles and quotes are scraped text: HTML from them would be code."""
    for script in (UI / "assets").glob("*.js"):
        source = script.read_text("utf-8")
        for dangerous in (
            "innerHTML",
            "outerHTML",
            "insertAdjacentHTML",
            "document.write",
        ):
            assert dangerous not in source, (script.name, dangerous)


# -- the pages ------------------------------------------------------------------


@pytest.fixture
def lisa(database):
    return database.create_user(email=EMAIL, display_name="Lisa")


def test_without_a_session_the_dashboard_sends_you_to_the_way_in(
    database, lisa, tmp_path
):
    with app_for(database, tmp_path, as_=None) as http:
        answer = http.get("/", follow_redirects=False)

    assert answer.status_code == 303 and answer.headers["location"] == "/login"


def test_a_page_comes_in_the_browsers_language_with_its_security_headers(
    database, lisa, tmp_path
):
    with app_for(database, tmp_path) as http:
        dutch = http.get("/", headers={"Accept-Language": "nl-NL,nl;q=0.9"})
        english = http.get("/", headers={"Accept-Language": "en-GB"})
        login = http.get("/login", headers={"Accept-Language": "nl"})

    assert '<html lang="nl">' in dutch.text and '<html lang="en">' in english.text
    assert 'content="en,nl"' in dutch.text  # the languages the switcher offers
    policy = dutch.headers["Content-Security-Policy"]
    assert "script-src 'self'" in policy and "unsafe-inline" not in policy
    assert "frame-ancestors 'none'" in policy
    assert login.headers["Referrer-Policy"] == "no-referrer"
    assert '<html lang="nl">' in login.text


def test_the_pages_files_are_served_and_the_api_docs_keep_working(
    database, lisa, tmp_path
):
    with app_for(database, tmp_path) as http:
        css = http.get("/assets/app.css")
        font = http.get("/assets/fonts/plus-jakarta-sans-latin.woff2")
        words = http.get("/assets/i18n/nl.json")
        docs = http.get("/api/docs")

    assert css.status_code == 200 and "text/css" in css.headers["content-type"]
    assert font.status_code == 200 and len(font.content) > 10_000
    assert words.json()["nav.dashboard"] == "Dashboard"
    assert docs.status_code == 200
    assert "Content-Security-Policy" not in docs.headers  # it loads from a CDN


def test_a_new_account_starts_in_the_guide_until_it_is_done_or_skipped(
    database, lisa, tmp_path
):
    with app_for(database, tmp_path) as http:
        first = http.get("/", follow_redirects=False)
        guide = http.get("/guide")
        refused = http.patch("/api/me", json={"onboarded": False})
        done = http.patch("/api/me", json={"onboarded": True})
        after = http.get("/", follow_redirects=False)

    assert first.status_code == 303 and first.headers["location"] == "/guide"
    assert guide.status_code == 200 and 'id="step-1"' in guide.text
    assert refused.status_code == 422  # a guide is not un-done
    assert done.json()["onboarded_at"] is not None
    assert after.status_code == 200 and 'id="topbar"' in after.text


def test_an_account_that_already_has_a_cv_is_not_sent_to_the_guide(
    database, lisa, tmp_path
):
    """Accounts from before the guide: they have a CV, so they are past step 1."""
    client = FakeClient(json.dumps(PROFILE))
    with app_for(database, tmp_path, chat=lambda s: client) as http:
        upload(http)
        home = http.get("/", follow_redirects=False)

    assert home.status_code == 200
    assert database.user(lisa.id).onboarded_at is None  # nothing was marked


@pytest.mark.parametrize("path", ["/guide", "/matches", "/cv", "/preferences"])
def test_every_page_needs_a_session_and_comes_in_your_language(
    database, lisa, tmp_path, path
):
    with app_for(database, tmp_path, as_=None) as http:
        outside = http.get(path, follow_redirects=False)
    with app_for(database, tmp_path) as http:
        inside = http.get(path, headers={"Accept-Language": "nl-NL"})

    assert outside.status_code == 303 and outside.headers["location"] == "/login"
    assert inside.status_code == 200 and '<html lang="nl">' in inside.text
    assert "script-src 'self'" in inside.headers["Content-Security-Policy"]


def test_the_home_field_suggests_places_the_map_knows(database, lisa, tmp_path):
    with app_for(database, tmp_path, as_=None) as http:
        outside = http.get("/api/places")
    with app_for(database, tmp_path) as http:
        answer = http.get("/api/places")
        spoken = http.put("/api/preferences", json={"home": "Den Haag"})

    places = answer.json()
    assert outside.status_code == 401
    # No API answer is kept by a browser (they hold CV text), refused or not.
    assert answer.headers["Cache-Control"] == outside.headers["Cache-Control"]
    assert answer.headers["Cache-Control"] == "no-store"
    assert {"Utrecht", "Den Haag", "'s-Gravenhage"} <= set(places)
    assert len(places) == len(set(places)) > 2000
    assert spoken.status_code == 200  # what it suggests, the server accepts


def test_the_switcher_keeps_the_language_on_the_account(database, lisa, tmp_path):
    with app_for(database, tmp_path) as http:
        changed = http.patch("/api/me", json={"locale": "en"})
        page = http.get("/", headers={"Accept-Language": "nl-NL"})
        refused = http.patch("/api/me", json={"locale": "xx"})

    assert changed.json()["locale"] == "en"
    assert '<html lang="en">' in page.text  # the choice beats the browser
    assert refused.status_code == 422


# -- what the dashboard is sent ---------------------------------------------------


def test_a_new_account_gets_an_empty_dashboard(database, lisa, tmp_path):
    with app_for(database, tmp_path) as http:
        board = http.get("/api/dashboard").json()

    assert board["user"]["display_name"] == "Lisa"
    assert board["cv"] is None and board["latest"] is None
    assert board["open_match"] is None
    assert board["usage"]["allowance_usd"] == 1.0
    assert board["ai"]["using"] == "joblens"


def test_after_a_match_the_dashboard_shows_its_best_with_quote_and_gap(
    database, lisa, tmp_path
):
    judgement = json.loads(JUDGEMENT)
    judgement["evidence"] = [{"requirement": "zorg", "cv_quote": "Verpleegkundige"}]
    judgement["gaps"] = [
        {
            "requirement": "BIG-registratie",
            "vacancy_quote": "Verpleegkundige",
            "required": True,
        }
    ]
    answer = json.dumps(judgement)
    client = FakeClient(json.dumps(PROFILE), WISHLIST, answer, answer, answer)
    with app_for(database, tmp_path, chat=lambda s: client) as http:
        upload(http)
        http.put("/api/preferences", json={"avoid_employers": ["Coolblue"]})
        http.post("/api/matches")
        board = http.get("/api/dashboard").json()

    latest = board["latest"]
    assert board["cv"]["filename"] == SANNE.name
    # All three are judged (a match reads ten; this corpus has three), the
    # Coolblue one after being moved behind the other two.
    assert (latest["strong"], latest["possible"], latest["weak"]) == (3, 0, 0)
    assert latest["moved"] == 1
    first = latest["recommended"][0]
    assert first["verdict"] == "strong" and first["fit"] == 80
    assert first["quote"] == "Verpleegkundige"
    assert (first["gap"], first["gap_required"]) == ("BIG-registratie", True)
    assert first["company"] == "Altrecht"


def test_the_dashboard_only_shows_matches_of_the_active_cv(database, lisa, tmp_path):
    """An import can bring other CVs' runs into an account; they stay out."""
    from test_storage import record

    store = database.store_for(lisa.id)
    store.save_run(record(cv="somebody_else"))
    client = FakeClient(json.dumps(PROFILE))
    with app_for(database, tmp_path, chat=lambda s: client) as http:
        upload(http)
        board = http.get("/api/dashboard").json()

    assert board["cv"] is not None and board["latest"] is None
