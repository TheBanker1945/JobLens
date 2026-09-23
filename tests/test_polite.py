"""The gate every request passes: pacing, budgets, refusals and their memory.

A fake clock and a fake sleep, so no test waits; a fake transport, so no test
leaves the machine.
"""

import argparse
import json
import tomllib
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest
from fetch_vacancies import fetch_into  # scripts/, put on the path by conftest.py

from joblens.sources.http import RateLimited, get_json, new_client, retry_after_seconds
from joblens.sources.polite import (
    CoolingDown,
    FetchState,
    Gate,
    OverBudget,
    PoliteTransport,
    Refused,
    Rules,
    site_of,
)
from joblens.sources.recruitee import RecruiteeSource
from joblens.sources.report import SearchRun
from joblens.sources.scraped import IndeedSource, LinkedInSource
from joblens.sources.store import VacancyStore

NOW = datetime(2026, 9, 23, 3, 0, tzinfo=UTC)  # a nightly run
ROOT = Path(__file__).parent.parent


class FakeTime:
    """A monotonic clock that only moves when something sleeps."""

    def __init__(self):
        self.t = 1000.0
        self.slept: list[float] = []

    def clock(self) -> float:
        return self.t

    def sleep(self, seconds: float) -> None:
        self.slept.append(round(seconds, 6))
        self.t += seconds


def make_gate(state=None, rules=None, now=NOW, jitter=0.0):
    fake = FakeTime()
    gate = Gate(
        state if state is not None else FetchState(),
        rules or Rules(jitter_seconds=0),
        sleep=fake.sleep,
        clock=fake.clock,
        now=lambda: now,
        jitter=lambda: jitter,
    )
    return gate, fake


def polite_client(gate: Gate, handler) -> httpx.Client:
    return new_client(transport=PoliteTransport(gate, httpx.MockTransport(handler)))


# --- what a site is -------------------------------------------------------------


def test_boards_on_one_platform_are_one_site():
    assert site_of("channable.recruitee.com") == "recruitee.com"
    assert site_of("nmbrs.recruitee.com") == "recruitee.com"
    assert site_of("boards-api.greenhouse.io") == "greenhouse.io"
    assert site_of("www.werkenbijdeoverheid.nl") == "werkenbijdeoverheid.nl"
    assert site_of("WWW.Example.NL.") == "example.nl"


# --- pacing ---------------------------------------------------------------------


def test_the_first_request_to_a_site_does_not_wait():
    gate, fake = make_gate()

    gate.ask("recruitee.com")

    assert fake.slept == []


def test_a_second_request_to_the_same_site_waits_the_delay():
    gate, fake = make_gate()

    gate.ask("recruitee.com")
    gate.answered("recruitee.com")
    gate.ask("recruitee.com")

    assert fake.slept == [1.5]


def test_time_already_spent_counts_towards_the_delay():
    gate, fake = make_gate()
    gate.ask("recruitee.com")
    gate.answered("recruitee.com")
    fake.t += 1.0  # parsing the answer took a second

    gate.ask("recruitee.com")

    assert fake.slept == [0.5]


def test_different_sites_do_not_wait_for_each_other():
    """The old loop slept 1.5 s between Adyen's Greenhouse board and Channable's
    Recruitee board, which share nothing."""
    gate, fake = make_gate()

    for site in ("greenhouse.io", "recruitee.com", "jobdataapi.com"):
        gate.ask(site)
        gate.answered(site)

    assert fake.slept == []


def test_jitter_adds_at_most_its_maximum():
    gate, fake = make_gate(
        rules=Rules(delay_seconds=1.5, jitter_seconds=1.0), jitter=1.0
    )

    gate.ask("recruitee.com")
    gate.answered("recruitee.com")
    gate.ask("recruitee.com")

    assert fake.slept == [2.5]


def test_a_site_can_have_its_own_delay():
    rules = Rules(jitter_seconds=0, sites={"slow.nl": {"delay_seconds": 10}})
    gate, fake = make_gate(rules=rules)

    for _ in range(2):
        gate.ask("slow.nl")
        gate.answered("slow.nl")

    assert fake.slept == [10]


# --- budget ---------------------------------------------------------------------


def test_a_run_stops_at_the_budget_for_that_site():
    gate, _ = make_gate(rules=Rules(jitter_seconds=0, max_requests_per_site=2))
    gate.ask("indeed.com")
    gate.ask("indeed.com")

    with pytest.raises(OverBudget) as info:
        gate.ask("indeed.com")

    assert info.value.status == "over_budget"
    gate.ask("recruitee.com")  # another site has its own budget
    assert gate.requests == {"indeed.com": 2, "recruitee.com": 1}


def test_the_budgets_in_sources_toml_are_read():
    config = tomllib.loads((ROOT / "sources.toml").read_text(encoding="utf-8"))

    rules = Rules.from_config(config["politeness"])

    assert rules.budget("jobdataapi.com") == 8  # its anonymous tier: ~10 an hour
    assert rules.budget("recruitee.com") == rules.max_requests_per_site


# --- refusals, and remembering them ------------------------------------------------


def test_a_refusal_is_written_to_disk_at_once(tmp_path):
    path = tmp_path / "fetch-state.json"
    gate, _ = make_gate(FetchState.load(path))

    until = gate.refused("jobdataapi.com", "HTTP 429")

    written = json.loads(path.read_text(encoding="utf-8"))["sites"]["jobdataapi.com"]
    assert until == NOW + timedelta(hours=12)
    assert written["strikes"] == 1
    assert written["reason"] == "HTTP 429"
    assert FetchState.load(path).sites["jobdataapi.com"].blocked_until == until


def test_the_next_run_does_not_ask_a_site_that_refused_the_last_one(tmp_path):
    path = tmp_path / "fetch-state.json"
    last_night, _ = make_gate(FetchState.load(path))
    last_night.refused("linkedin.com", "descriptions stopped arriving")

    an_hour_later, _ = make_gate(FetchState.load(path), now=NOW + timedelta(hours=1))
    with pytest.raises(CoolingDown) as info:
        an_hour_later.ask("linkedin.com")

    assert info.value.status == "cooling_down"
    assert "descriptions stopped arriving" in info.value.detail
    assert "2026-09-23 15:00 UTC" in info.value.detail
    assert an_hour_later.requests["linkedin.com"] == 0


def test_after_the_cooldown_the_site_is_asked_again(tmp_path):
    path = tmp_path / "fetch-state.json"
    make_gate(FetchState.load(path))[0].refused("linkedin.com", "HTTP 429")

    next_night, _ = make_gate(FetchState.load(path), now=NOW + timedelta(hours=24))

    next_night.ask("linkedin.com")  # does not raise


def test_each_refusal_in_a_row_doubles_the_wait_up_to_a_week():
    gate, _ = make_gate()

    waits = [gate.refused("x.nl", "HTTP 403") - NOW for _ in range(6)]

    hours = [wait / timedelta(hours=1) for wait in waits]
    assert hours == [12, 24, 48, 96, 168, 168]


def test_a_longer_retry_after_than_our_schedule_wins():
    gate, _ = make_gate()

    until = gate.refused("x.nl", "HTTP 429", retry_after=2 * 24 * 3600)

    assert until == NOW + timedelta(days=2)


def test_a_shorter_retry_after_does_not_shorten_our_schedule():
    """jobdataapi asked for 2710 s in 2.3; a nightly run waits till the next night."""
    gate, _ = make_gate()

    until = gate.refused("jobdataapi.com", "HTTP 429", retry_after=2710)

    assert until == NOW + timedelta(hours=12)


def test_a_clean_run_forgives_a_site(tmp_path):
    path = tmp_path / "fetch-state.json"
    make_gate(FetchState.load(path))[0].refused("jobdataapi.com", "HTTP 429")

    next_night, _ = make_gate(FetchState.load(path), now=NOW + timedelta(hours=24))
    next_night.ask("jobdataapi.com")
    next_night.answered("jobdataapi.com")
    next_night.finish()

    assert FetchState.load(path).sites == {}


def test_a_site_that_refused_again_in_this_run_is_not_forgiven(tmp_path):
    """One board answered, the next one on the same platform refused."""
    path = tmp_path / "fetch-state.json"
    gate, _ = make_gate(FetchState.load(path))
    gate.ask("recruitee.com")
    gate.answered("recruitee.com")
    gate.refused("recruitee.com", "HTTP 403")

    gate.finish()

    assert FetchState.load(path).sites["recruitee.com"].strikes == 1


def test_a_site_this_run_never_asked_is_not_forgiven(tmp_path):
    """`--source indeed` says nothing about LinkedIn."""
    path = tmp_path / "fetch-state.json"
    make_gate(FetchState.load(path))[0].refused("linkedin.com", "HTTP 429")

    gate, _ = make_gate(FetchState.load(path), now=NOW + timedelta(days=3))
    gate.ask("indeed.com")
    gate.answered("indeed.com")
    gate.finish()

    assert "linkedin.com" in FetchState.load(path).sites


# --- the transport: every request, without the adapters knowing ----------------------

BOARD = {"offers": [{"id": 1, "title": "Python Developer", "description": "<p>x</p>"}]}
CLOUDFLARE_CHALLENGE = (
    "<!DOCTYPE html><html><head><title>Just a moment...</title></head><body>"
    "<script>window._cf_chl_opt={cvId: '3'};</script></body></html>"
)
CLOUDFLARE_ORDINARY_PAGE = (
    "<html><head><title>Vacatures</title></head><body><h1>Python Developer</h1>"
    '<script src="/cdn-cgi/challenge-platform/scripts/jsd/main.js"></script>'
    "</body></html>"
)
DPG_CONSENT_WALL = "<html><head><title>DPG Media Privacy Gate</title></head></html>"


def html(status: int, body: str) -> httpx.Response:
    return httpx.Response(status, text=body, headers={"content-type": "text/html"})


def test_the_adapters_work_unchanged_through_the_gate():
    gate, fake = make_gate()
    client = polite_client(gate, lambda request: httpx.Response(200, json=BOARD))

    for slug in ("channable", "nmbrs"):
        assert len(RecruiteeSource(slug, client).fetch()) == 1

    assert gate.requests == {"recruitee.com": 2}
    assert fake.slept == [1.5]  # two boards, one platform: one pause


def test_a_429_is_raised_as_before_and_remembered():
    gate, _ = make_gate()
    client = polite_client(
        gate, lambda request: httpx.Response(429, headers={"Retry-After": "2710"})
    )

    with pytest.raises(RateLimited) as info:
        client.get("https://jobdataapi.com/api/jobs/")

    assert info.value.retry_after == 2710
    assert gate.state.sites["jobdataapi.com"].reason == "HTTP 429"


def test_after_a_refusal_the_same_run_does_not_ask_that_site_again():
    """Greenhouse refusing Adyen's board must not be asked for Catawiki's next."""
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(429)

    gate, _ = make_gate()
    client = polite_client(gate, handler)
    with pytest.raises(RateLimited):
        client.get("https://boards-api.greenhouse.io/v1/boards/adyen/jobs")

    with pytest.raises(CoolingDown):
        client.get("https://boards-api.greenhouse.io/v1/boards/catawiki/jobs")

    assert len(seen) == 1


def test_a_403_is_a_refusal():
    gate, _ = make_gate()
    client = polite_client(gate, lambda request: httpx.Response(403))

    with pytest.raises(Refused) as info:
        client.get("https://www.adzuna.nl/robots.txt")

    assert info.value.status == "blocked"
    assert info.value.detail.startswith("HTTP 403")
    assert gate.state.sites["adzuna.nl"].strikes == 1


def test_a_cloudflare_challenge_is_named():
    """werkzoeken.nl, 2026-09-22: a 403 with this page, even for robots.txt."""
    gate, _ = make_gate()
    client = polite_client(gate, lambda request: html(403, CLOUDFLARE_CHALLENGE))

    with pytest.raises(Refused) as info:
        client.get("https://www.werkzoeken.nl/robots.txt")

    assert "Cloudflare challenge" in info.value.detail


def test_a_consent_wall_that_answers_200_is_a_refusal():
    """nationalevacaturebank.nl, 2026-09-22: HTTP 200, and no vacancy on it."""
    gate, _ = make_gate()
    client = polite_client(gate, lambda request: html(200, DPG_CONSENT_WALL))

    with pytest.raises(Refused) as info:
        client.get("https://www.nationalevacaturebank.nl/vacature/0001")

    assert "consent wall" in info.value.detail


def test_a_redirect_to_a_consent_wall_is_held_against_the_site_we_asked():
    """nationalevacaturebank.nl, 2026-09-22: every vacancy answers 302 to DPG's
    consent host. Following it would blame dpgmedia.nl and call NVB fine."""
    wall = "https://myprivacy.dpgmedia.nl/consent?siteKey=x&callbackUrl=y"
    gate, _ = make_gate()
    client = polite_client(
        gate, lambda request: httpx.Response(302, headers={"location": wall})
    )

    with pytest.raises(Refused) as info:
        client.get("https://www.nationalevacaturebank.nl/vacature/0001")

    assert "consent wall" in info.value.detail
    assert set(gate.state.sites) == {"nationalevacaturebank.nl"}


def test_an_ordinary_redirect_is_not_a_refusal():
    gate, _ = make_gate()
    client = polite_client(
        gate,
        lambda request: httpx.Response(
            301, headers={"location": "https://jobs.channable.com/o/csm"}
        ),
    )

    assert client.get("https://channable.recruitee.com/o/csm").status_code == 301
    assert gate.state.sites == {}


def test_an_ordinary_page_on_a_cloudflare_site_is_not_a_refusal():
    """Cloudflare loads a challenge-platform script on normal pages too."""
    gate, _ = make_gate()
    client = polite_client(gate, lambda request: html(200, CLOUDFLARE_ORDINARY_PAGE))

    response = client.get("https://werkenbij.example.nl/vacature/1")

    assert "Python Developer" in response.text
    assert gate.state.sites == {}


def test_a_board_that_does_not_exist_is_not_a_refusal():
    gate, _ = make_gate()
    client = polite_client(gate, lambda request: httpx.Response(404))

    with pytest.raises(httpx.HTTPStatusError):
        get_json(client, "https://gone.recruitee.com/api/offers/", "recruitee")

    assert gate.state.sites == {}


def test_a_timeout_is_not_a_refusal_but_still_paces_the_next_request():
    calls = []

    def handler(request):
        calls.append(request)
        if len(calls) == 1:
            raise httpx.ConnectTimeout("slow", request=request)
        return httpx.Response(200, json=BOARD)

    gate, fake = make_gate()
    client = polite_client(gate, handler)
    with pytest.raises(httpx.ConnectTimeout):
        client.get("https://a.recruitee.com/api/offers/")

    client.get("https://b.recruitee.com/api/offers/")

    assert gate.state.sites == {}
    assert fake.slept == [1.5]


# --- Retry-After, in both of its forms ----------------------------------------------


def test_retry_after_in_seconds():
    assert retry_after_seconds(httpx.Headers({"Retry-After": "2710"})) == 2710


def test_retry_after_as_a_date():
    headers = httpx.Headers({"Retry-After": "Wed, 23 Sep 2026 05:00:00 GMT"})

    assert retry_after_seconds(headers, now=NOW) == 7200


def test_retry_after_that_cannot_be_read_is_ignored():
    assert retry_after_seconds(httpx.Headers({"Retry-After": "soon"})) is None
    assert retry_after_seconds(httpx.Headers()) is None


def test_a_date_in_retry_after_no_longer_crashes_the_run():
    """get_json did float(header), which raises on the date form."""
    headers = {"Retry-After": "Wed, 23 Sep 2099 05:00:00 GMT"}
    client = new_client(
        transport=httpx.MockTransport(lambda r: httpx.Response(429, headers=headers))
    )

    with pytest.raises(RateLimited) as info:
        get_json(client, "https://jobdataapi.com/api/jobs/", "jobdataapi")

    assert info.value.retry_after > 0


# --- the scraped sources: JobSpy sends its own requests, so the script asks --------

ARGS = argparse.Namespace(limit=50, all_countries=False)
BODY = "Je bouwt datapijplijnen in Python en SQL voor het team. " * 6
ROW = {
    "id": "in-3fa1",
    "job_url": "https://nl.indeed.com/viewjob?jk=3fa1",
    "title": "Data Engineer",
    "company": "Adyen",
    "location": "Amsterdam, North Holland, Netherlands",
    "description": f"<p>{BODY}</p>",
}


def test_a_scraped_search_asks_the_gate_and_is_paced(tmp_path):
    gate, fake = make_gate()
    store = VacancyStore(tmp_path)

    for term in ("data engineer", "python developer"):
        source = IndeedSource(term, "Amsterdam", scrape=lambda **options: [ROW])
        run = SearchRun("indeed", term)
        assert fetch_into(run, source, gate, {}, ARGS, store, ["Amsterdam"])

    assert gate.requests == {"indeed.com": 2}
    assert fake.slept == [1.5]  # between the searches, as the old loop did


def test_a_scraped_site_that_is_cooling_down_is_not_scraped(tmp_path):
    gate, _ = make_gate()
    gate.refused("indeed.com", "all searches came back empty")
    scraped = []
    source = IndeedSource(
        "data engineer", "Amsterdam", scrape=lambda **o: scraped.append(o) or [ROW]
    )
    run = SearchRun("indeed", "data engineer in Amsterdam")

    assert not fetch_into(run, source, gate, {}, ARGS, VacancyStore(tmp_path), [])

    assert run.status == "cooling_down"
    assert scraped == []


def test_linkedin_throttling_is_remembered_for_the_next_run(tmp_path):
    """Descriptions that stop arriving are LinkedIn saying no without a 429."""
    path = tmp_path / "fetch-state.json"
    gate, _ = make_gate(FetchState.load(path))
    rows = [{**ROW, "id": f"li-{n}", "site": "linkedin"} for n in range(6)]
    empty_page = lambda request: html(200, "<html><body>log in</body></html>")  # noqa: E731
    source = LinkedInSource(
        "data engineer",
        "Amsterdam, Netherlands",
        polite_client(gate, empty_page),
        delay_seconds=0,
        scrape=lambda **options: rows,
    )
    run = SearchRun("linkedin", "data engineer in Amsterdam")

    assert not fetch_into(run, source, gate, {}, ARGS, VacancyStore(tmp_path), [])

    assert run.status == "throttled"
    remembered = FetchState.load(path).sites["linkedin.com"]
    assert remembered.reason == "descriptions stopped arriving"


def test_a_bug_in_one_adapter_fails_its_search_and_nothing_else(tmp_path, capsys):
    """2026-09-23: a KeyError in the Workday adapter stopped the first real run
    halfway, before the other sources, the sightings and the report."""

    class Broken:
        name = "workday"

        def fetch(self, limit=None):
            raise KeyError("externalPath")

    gate, _ = make_gate()
    run = SearchRun("workday", "philips.wd3/jobs-and-careers")

    assert not fetch_into(run, Broken(), gate, {}, ARGS, VacancyStore(tmp_path), [])

    assert run.status == "failed"
    assert run.detail == "KeyError: 'externalPath'"
    assert "Traceback" in capsys.readouterr().err  # the log keeps the stack
