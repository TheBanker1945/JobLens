"""Scraped sources with a fake JobSpy and a fake LinkedIn: no network, no library.

The adapters take their scraper as an argument, so these tests never import
JobSpy (it lives in the optional `scrape` group) and never leave the machine.
"""

import time
from datetime import date, datetime

import httpx
import pytest

from joblens.sources.http import RateLimited, new_client
from joblens.sources.scraped import (
    IndeedSource,
    LikelyThrottled,
    LinkedInSource,
    ScrapeTimeout,
    run_with_deadline,
)

BODY = "Je bouwt datapijplijnen in Python en SQL voor het team. " * 6  # > 200 chars

INDEED_ROW = {
    "id": "in-3fa1",
    "site": "indeed",
    "job_url": "https://nl.indeed.com/viewjob?jk=3fa1",
    "title": " Data Engineer ",
    "company": "Adyen",
    "location": "Amsterdam, North Holland, Netherlands",
    "date_posted": date(2026, 9, 18),
    "emails": "recruiter@adyen.com",  # JobSpy harvests these out of the description
    "description": f"<p>{BODY}</p><p>Vragen? Bel 06-12345678</p>",
}
LINKEDIN_ROW = {
    "id": "li-4456868563",
    "site": "linkedin",
    "job_url": "https://www.linkedin.com/jobs/view/4456868563",
    "title": "Data Engineer",
    "company": "Devoteam",
    "location": "Amsterdam, North Holland",  # guest pages give no country
    "date_posted": "2026-09-19",
}
FRAGMENT = (
    "<section><div class='show-more-less-html__markup relative'>"
    f"<p>{BODY}</p></div></section>"
)


def fake_scrape(rows: list[dict], seen: list | None = None):
    """Stands in for jobspy.scrape_jobs: records the options, returns the rows."""

    def scrape(**options):
        if seen is not None:
            seen.append(options)
        return [dict(row) for row in rows]

    return scrape


def fake_linkedin(body: str = FRAGMENT, seen=None, status: int = 200, headers=None):
    def handler(request: httpx.Request) -> httpx.Response:
        if seen is not None:
            seen.append(request)
        return httpx.Response(status, text=body, headers=headers or {})

    return new_client(transport=httpx.MockTransport(handler))


def linkedin_source(rows, client, **kwargs):
    kwargs.setdefault("delay_seconds", 0)  # tests should not sit and wait
    return LinkedInSource(
        "data engineer", "Amsterdam", client, scrape=fake_scrape(rows), **kwargs
    )


def test_indeed_row_becomes_a_vacancy():
    source = IndeedSource(
        "data engineer", "Amsterdam", scrape=fake_scrape([INDEED_ROW])
    )

    vacancy = source.fetch()[0]

    assert vacancy.key == "indeed:3fa1"  # JobSpy's site prefix is dropped
    assert vacancy.title == "Data Engineer"
    assert vacancy.city == "Amsterdam" and vacancy.country == "NL"
    assert vacancy.posted_at == datetime(2026, 9, 18)
    assert "datapijplijnen" in vacancy.text
    assert "[phone removed]" in vacancy.text  # clean.py ran over the description


def test_stored_row_keeps_no_contact_details():
    source = IndeedSource(
        "data engineer", "Amsterdam", scrape=fake_scrape([INDEED_ROW])
    )

    raw = source.fetch()[0].raw

    assert "emails" not in raw  # harvested recruiter addresses, never stored
    assert "description" not in raw  # the un-redacted original, never stored
    assert raw["date_posted"] == "2026-09-18"  # JSON-safe
    assert raw["location"] == "Amsterdam, North Holland, Netherlands"


def test_indeed_asks_in_miles_and_for_html():
    seen = []
    source = IndeedSource(
        "data engineer",
        "Amsterdam",
        distance_km=25,
        scrape=fake_scrape([INDEED_ROW], seen),
    )

    source.fetch(limit=30)

    assert seen[0]["distance"] == 16  # 25 km, not JobSpy's 50-mile default
    assert seen[0]["country_indeed"] == "netherlands"
    assert seen[0]["description_format"] == "html"
    assert seen[0]["results_wanted"] == 30


def test_jobs_without_a_real_description_are_dropped():
    thin = {**INDEED_ROW, "description": "<p>Interesse? Solliciteer!</p>"}
    source = IndeedSource("x", "Amsterdam", scrape=fake_scrape([INDEED_ROW, thin]))

    vacancies = source.fetch()

    assert len(vacancies) == 1
    assert source.stats.dropped_no_text == 1
    assert source.stats.kept == 1 and source.stats.listed == 2


def test_rows_without_an_id_or_url_are_dropped():
    broken = {**INDEED_ROW, "id": None}
    source = IndeedSource("x", "Amsterdam", scrape=fake_scrape([broken]))

    assert source.fetch() == []
    assert source.stats.dropped_invalid == 1


def test_linkedin_asks_for_the_listing_only():
    seen = []
    source = LinkedInSource(
        "data engineer",
        "Amsterdam",
        fake_linkedin(),
        delay_seconds=0,
        scrape=fake_scrape([LINKEDIN_ROW], seen),
    )

    source.fetch()

    assert seen[0]["linkedin_fetch_description"] is False


def test_linkedin_fetches_the_description_itself():
    requests = []
    source = linkedin_source([LINKEDIN_ROW], fake_linkedin(seen=requests))

    vacancy = source.fetch()[0]

    assert str(requests[0].url) == (
        "https://www.linkedin.com/jobs-guest/jobs/api/jobPosting/4456868563"
    )
    assert requests[0].headers["user-agent"].startswith("JobLens/")
    assert "datapijplijnen" in vacancy.text
    assert vacancy.key == "linkedin:4456868563" and vacancy.country is None


def test_known_jobs_cost_no_request():
    requests = []
    source = linkedin_source(
        [LINKEDIN_ROW],
        fake_linkedin(seen=requests),
        known_keys={"linkedin:4456868563"},
    )

    assert source.fetch() == []
    assert requests == []  # the whole point: no description request at all
    assert source.stats.skipped_known == 1


def test_a_run_never_exceeds_its_description_budget():
    rows = [{**LINKEDIN_ROW, "id": f"li-{n}"} for n in range(5)]
    requests = []
    source = linkedin_source(rows, fake_linkedin(seen=requests), max_descriptions=2)

    assert len(source.fetch()) == 2
    assert len(requests) == 2


def test_empty_descriptions_stop_the_run_as_throttling():
    rows = [{**LINKEDIN_ROW, "id": f"li-{n}"} for n in range(20)]
    requests = []
    source = linkedin_source(rows, fake_linkedin("<html>sign in</html>", requests))

    with pytest.raises(LikelyThrottled) as info:
        source.fetch()

    assert len(requests) == 5  # stopped at the first sign, did not work the list
    assert info.value.empty == 5


def test_a_single_empty_description_is_not_throttling():
    rows = [{**LINKEDIN_ROW, "id": f"li-{n}"} for n in range(4)]

    def handler(request: httpx.Request) -> httpx.Response:
        # only the first job answers without a description div
        body = "<html>sign in</html>" if str(request.url).endswith("/0") else FRAGMENT
        return httpx.Response(200, text=body)

    source = linkedin_source(rows, new_client(transport=httpx.MockTransport(handler)))

    vacancies = source.fetch()

    assert len(vacancies) == 3
    assert source.stats.dropped_no_text == 1


def test_linkedin_429_stops_immediately():
    rows = [{**LINKEDIN_ROW, "id": f"li-{n}"} for n in range(5)]
    requests = []
    client = fake_linkedin("", requests, status=429, headers={"Retry-After": "600"})
    source = linkedin_source(rows, client)

    with pytest.raises(RateLimited) as info:
        source.fetch()

    assert info.value.retry_after == 600
    assert len(requests) == 1  # no retry, no working through the rest


def test_a_scrape_that_never_returns_is_abandoned():
    def forever():
        time.sleep(30)
        return []

    with pytest.raises(ScrapeTimeout):
        run_with_deadline(forever, 0.05, "indeed")


def test_an_error_inside_the_scrape_reaches_the_caller():
    def broken():
        raise httpx.ConnectError("no route to host")

    with pytest.raises(httpx.ConnectError):
        run_with_deadline(broken, 5, "indeed")
