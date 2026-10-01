"""Scraped sources with a fake JobSpy and a fake LinkedIn: no network, no library.

The Indeed adapter takes its scraper as an argument, so these tests never import
JobSpy (it lives in the optional `scrape` group). LinkedIn is read through an
httpx client, so a MockTransport stands in for it. Nothing leaves the machine.
"""

import time
from datetime import date, datetime
from pathlib import Path

import httpx
import pytest

from joblens.sources.http import RateLimited, new_client
from joblens.sources.scope import Scope
from joblens.sources.scraped import (
    IndeedSource,
    LikelyThrottled,
    LinkedInRun,
    LinkedInSource,
    ScrapeError,
    ScrapeTimeout,
    job_cards,
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
FRAGMENT = (
    "<section><div class='show-more-less-html__markup relative'>"
    f"<p>{BODY}</p></div></section>"
)


def card(
    job_id: int,
    title: str = "Data Engineer",
    place: str = "Amsterdam, North Holland, Netherlands",
) -> str:
    """One job card as LinkedIn's guest search wrote it on 2026-10-01, trimmed."""
    return f"""<li>
<div class="base-card relative base-card--link base-search-card job-search-card"
  data-entity-urn="urn:li:jobPosting:{job_id}" data-row="1">
  <a class="base-card__full-link" href="https://nl.linkedin.com/jobs/view/x-{job_id}">
    <span class="sr-only">{title}</span></a>
  <div class="base-search-card__info">
    <h3 class="base-search-card__title">
      {title}
    </h3>
    <h4 class="base-search-card__subtitle">
      <a class="hidden-nested-link" href="/company/x">Devoteam &amp; Co</a>
    </h4>
    <div class="base-search-card__metadata">
      <span class="job-search-card__location">
        {place}
      </span>
      <time class="job-search-card__listdate" datetime="2026-09-19">2 days ago</time>
    </div>
  </div>
</div>
</li>"""


def page(*ids: int, **fields) -> str:
    return "".join(card(job_id, **fields) for job_id in ids)


def fake_scrape(rows: list[dict], seen: list | None = None):
    """Stands in for jobspy.scrape_jobs: records the options, returns the rows."""

    def scrape(**options):
        if seen is not None:
            seen.append(options)
        return [dict(row) for row in rows]

    return scrape


def fake_linkedin(
    pages=(),
    body: str = FRAGMENT,
    seen=None,
    status: int = 200,
    headers=None,
    search_status: int = 200,
):
    """Search pages in order (then empty ones), and `body` for every job."""
    pages = list(pages)

    def handler(request: httpx.Request) -> httpx.Response:
        if seen is not None:
            seen.append(request)
        if "seeMoreJobPostings" in request.url.path:
            text = pages.pop(0) if pages else ""
            return httpx.Response(search_status, text=text)
        return httpx.Response(status, text=body, headers=headers or {})

    return new_client(transport=httpx.MockTransport(handler))


def linkedin_source(client, **kwargs):
    kwargs.setdefault("delay_seconds", 0)  # tests should not sit and wait
    return LinkedInSource("data engineer", "Amsterdam, Netherlands", client, **kwargs)


def searches(requests: list[httpx.Request]) -> list[httpx.Request]:
    return [r for r in requests if "/seeMoreJobPostings/" in r.url.path]


def descriptions(requests: list[httpx.Request]) -> list[httpx.Request]:
    return [r for r in requests if "/jobPosting/" in r.url.path]


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


def test_a_search_page_becomes_rows_in_jobspys_shape():
    rows = job_cards(page(4456868563) + "<li><div class='base-card'>an ad</div></li>")

    assert rows == [
        {
            "id": "li-4456868563",
            "site": "linkedin",
            "job_url": "https://www.linkedin.com/jobs/view/4456868563",
            "title": "Data Engineer",
            "company": "Devoteam & Co",
            "location": "Amsterdam, North Holland, Netherlands",
            "date_posted": "2026-09-19",
        }
    ]


def test_the_real_search_page_of_2026_10_01_reads():
    """A real page, cut to its first card: the markup this parser was built on."""
    rows = job_cards(Path(__file__).with_name("linkedin_search_card.html").read_text())

    assert [row["title"] for row in rows] == ["Full-stack .NET ontwikkelaar"]
    assert rows[0]["company"] == "Respellion"
    assert rows[0]["location"] == "The Hague, South Holland, Netherlands"
    assert rows[0]["date_posted"] == "2026-09-28"


def test_linkedin_searches_itself_with_our_user_agent():
    requests = []
    source = linkedin_source(fake_linkedin([page(1)], seen=requests), hours_old=72)

    source.fetch()

    search = searches(requests)[0]
    assert search.url.path == "/jobs-guest/jobs/api/seeMoreJobPostings/search"
    assert search.url.params["keywords"] == "data engineer"
    assert search.url.params["location"] == "Amsterdam, Netherlands"
    assert search.url.params["distance"] == "16"  # 25 km, in LinkedIn's miles
    assert search.url.params["f_TPR"] == "r259200"  # 72 hours, in seconds
    assert search.headers["user-agent"].startswith("JobLens/")  # no disguise


def test_linkedin_fetches_the_description_itself():
    requests = []
    source = linkedin_source(fake_linkedin([page(4456868563)], seen=requests))

    vacancy = source.fetch()[0]

    assert str(descriptions(requests)[0].url) == (
        "https://www.linkedin.com/jobs-guest/jobs/api/jobPosting/4456868563"
    )
    assert descriptions(requests)[0].headers["user-agent"].startswith("JobLens/")
    assert "datapijplijnen" in vacancy.text
    assert vacancy.key == "linkedin:4456868563"
    assert vacancy.city == "Amsterdam" and vacancy.country == "NL"
    assert source.stats.listed_keys == {"linkedin:4456868563"}


def test_the_search_pages_until_a_short_page():
    requests = []
    pages = [page(*range(1, 11)), page(*range(11, 21)), page(21, 22)]
    source = linkedin_source(fake_linkedin(pages, seen=requests))

    source.fetch(limit=100)

    starts = [r.url.params["start"] for r in searches(requests)]
    assert starts == ["0", "10", "20"]  # the third page had two: the last
    assert source.stats.listed == 22


def test_the_search_asks_no_more_pages_than_the_limit_needs():
    requests = []
    pages = [page(*range(n, n + 10)) for n in (1, 11, 21, 31)]
    source = linkedin_source(fake_linkedin(pages, seen=requests))

    source.fetch(limit=25)

    assert len(searches(requests)) == 3
    assert source.stats.listed == 25


def test_known_jobs_cost_no_request():
    requests = []
    source = linkedin_source(
        fake_linkedin([page(4456868563)], seen=requests),
        known_keys={"linkedin:4456868563"},
    )

    assert source.fetch() == []
    assert descriptions(requests) == []  # the whole point: no description request
    assert source.stats.skipped_known == 1


def test_cards_outside_the_scope_cost_no_request():
    scope = Scope(["Utrecht"], ["engineer"], ["sales engineer"])
    requests = []
    cards = [
        card(1, title="Data Engineer", place="Utrecht, Utrecht, Netherlands"),
        card(2, title="Sales Engineer", place="Utrecht, Utrecht, Netherlands"),
        card(3, title="Data Engineer", place="Eindhoven, North Brabant, Netherlands"),
    ]
    client = fake_linkedin(["".join(cards)], seen=requests)
    source = linkedin_source(client, scope=scope)

    vacancies = source.fetch()

    assert [v.source_id for v in vacancies] == ["1"]
    assert len(descriptions(requests)) == 1
    assert source.stats.out_of_scope == 2
    assert len(source.stats.left_out) == 2
    assert len(source.stats.listed_keys) == 3  # listed is still open, in or out


def test_a_run_never_exceeds_its_description_budget():
    """The cap is per run: every search of the run draws from one allowance."""
    run = LinkedInRun(descriptions_left=3)
    requests = []
    client = fake_linkedin([page(1, 2), page(3, 4)], seen=requests)
    first = linkedin_source(client, run=run)
    second = linkedin_source(client, run=run)

    assert len(first.fetch()) == 2
    assert len(second.fetch()) == 1
    assert len(descriptions(requests)) == 3
    assert run.descriptions_left == 0


def test_empty_descriptions_stop_the_run_as_throttling():
    requests = []
    pages = [page(*range(1, 11)), page(*range(11, 21))]
    client = fake_linkedin(pages, "<html>sign in</html>", requests)
    source = linkedin_source(client)

    with pytest.raises(LikelyThrottled) as info:
        source.fetch(limit=20)

    assert len(descriptions(requests)) == 5  # stopped at the first sign
    assert info.value.empty == 5


def test_a_single_empty_description_is_not_throttling():
    def handler(request: httpx.Request) -> httpx.Response:
        if "seeMore" in request.url.path:
            return httpx.Response(200, text=page(1, 2, 3, 4))
        # only the first job answers without a description div
        body = "<html>sign in</html>" if request.url.path.endswith("/1") else FRAGMENT
        return httpx.Response(200, text=body)

    source = linkedin_source(new_client(transport=httpx.MockTransport(handler)))

    vacancies = source.fetch()

    assert len(vacancies) == 3
    assert source.stats.dropped_no_text == 1


def test_linkedin_429_stops_immediately():
    requests = []
    client = fake_linkedin(
        [page(1, 2, 3)], "", requests, status=429, headers={"Retry-After": "600"}
    )
    source = linkedin_source(client)

    with pytest.raises(RateLimited) as info:
        source.fetch()

    assert info.value.retry_after == 600
    assert len(descriptions(requests)) == 1  # no retry, no working through the rest


def test_a_search_error_stops_linkedin_for_the_rest_of_the_run():
    """An answer the gate does not know as a refusal (LinkedIn is said to send
    999): not remembered for later runs, but not asked again in this one."""
    run = LinkedInRun()
    requests = []
    client = fake_linkedin(seen=requests, search_status=999)

    with pytest.raises(ScrapeError, match="HTTP 999"):
        linkedin_source(client, run=run).fetch()
    with pytest.raises(ScrapeError, match="not asked again"):
        linkedin_source(client, run=run).fetch()

    assert len(requests) == 1  # once, and no retry


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
