"""Adapter tests against fake servers: the real payload shapes, no network."""

import httpx
import pytest

from joblens.sources.greenhouse import GreenhouseSource
from joblens.sources.http import RateLimited
from joblens.sources.jobdataapi import JobDataApiSource
from joblens.sources.recruitee import RecruiteeSource

RECRUITEE_PAYLOAD = {
    "offers": [
        {
            "id": 2751915,
            "title": " Customer Success Manager ",
            "company_name": "Channable",
            "city": "Utrecht",
            "country_code": "NL",
            "careers_url": "https://jobs.channable.com/o/csm",
            "created_at": "2026-09-18 12:55:59 UTC",
            "description": "<p>Channable is a SaaS platform.</p>",
            "requirements": "<ul><li>SQL</li></ul><p>Mail jan@channable.com</p>",
        },
        {"id": 2, "title": "Tweede", "description": "<p>x</p>", "requirements": ""},
    ]
}
GREENHOUSE_PAYLOAD = {
    "jobs": [
        {
            "id": 7586281,
            "title": " Account Manager",
            "company_name": "Adyen",
            "absolute_url": "https://job-boards.greenhouse.io/adyen/jobs/7586281",
            "location": {"name": "Amsterdam"},
            "updated_at": "2026-09-17T05:48:51-04:00",
            # Greenhouse escapes its HTML once more than you expect
            "content": "&lt;p&gt;&lt;strong&gt;This is Adyen&lt;/strong&gt;&lt;/p&gt;",
        }
    ]
}
JOBDATA_PAYLOAD = {
    "count": 5399,
    "results": [
        {
            "id": 99,
            "title": "Verpleegkundige",
            "company": {"name": "Zorggroep"},
            "locations": [{"city": "Nijmegen"}],
            "published": "2026-09-19T08:00:00Z",
            "application_url": "https://example.test/99",
            "description": "<div>Zorg voor ouderen. Bel 06-12345678</div>",
        }
    ],
}


def fake(payload, seen=None, status=200, headers=None):
    def handler(request: httpx.Request) -> httpx.Response:
        if seen is not None:
            seen.append(request)
        return httpx.Response(status, json=payload, headers=headers or {})

    return httpx.Client(transport=httpx.MockTransport(handler))


def test_recruitee_joins_description_and_requirements():
    seen = []
    source = RecruiteeSource("channable", fake(RECRUITEE_PAYLOAD, seen))

    vacancies = source.fetch()

    assert str(seen[0].url) == "https://channable.recruitee.com/api/offers/"
    first = vacancies[0]
    assert first.key == "recruitee:2751915"
    assert first.title == "Customer Success Manager"  # trimmed
    assert first.city == "Utrecht" and first.country == "NL"
    assert "Channable is a SaaS platform." in first.text
    assert "- SQL" in first.text  # the requirements section came along
    assert "jan@channable.com" not in first.text  # contact details removed
    assert "jan@channable.com" not in str(first.raw)  # also in the kept payload
    assert first.posted_at.year == 2026


def test_limit_is_respected():
    assert len(RecruiteeSource("x", fake(RECRUITEE_PAYLOAD)).fetch(limit=1)) == 1


def test_a_board_without_a_limit_returns_every_job():
    """One response holds the whole board; cutting it here only loses jobs
    the Dutch filter has not looked at yet (sources/netherlands.py)."""
    template = GREENHOUSE_PAYLOAD["jobs"][0]
    board = {"jobs": [template | {"id": n} for n in range(250)]}
    offers = {
        "offers": [RECRUITEE_PAYLOAD["offers"][1] | {"id": n} for n in range(150)]
    }

    assert len(GreenhouseSource("adyen", fake(board)).fetch()) == 250
    assert len(RecruiteeSource("x", fake(offers)).fetch()) == 150


def test_greenhouse_unescapes_double_escaped_html():
    vacancies = GreenhouseSource("adyen", fake(GREENHOUSE_PAYLOAD)).fetch()

    assert vacancies[0].text == "This is Adyen"  # not "&lt;p&gt;..."
    assert vacancies[0].city == "Amsterdam"
    assert vacancies[0].company == "Adyen"


def test_jobdataapi_sends_filters_and_cleans_text():
    seen = []
    source = JobDataApiSource(
        fake(JOBDATA_PAYLOAD, seen), filters=({"title": "verpleegkundige"},)
    )

    vacancies = source.fetch()

    assert seen[0].url.params["country_code"] == "NL"
    assert seen[0].url.params["title"] == "verpleegkundige"
    assert vacancies[0].city == "Nijmegen"
    assert "[phone removed]" in vacancies[0].text


def test_jobdataapi_uses_one_request_per_filter():
    seen = []
    source = JobDataApiSource(
        fake(JOBDATA_PAYLOAD, seen), filters=({"title": "a"}, {"title": "b"})
    )

    source.fetch()

    assert [r.url.params["title"] for r in seen] == ["a", "b"]


def test_rate_limit_stops_immediately_with_retry_after():
    seen = []
    client = fake({}, seen, status=429, headers={"Retry-After": "2710"})

    with pytest.raises(RateLimited) as info:
        JobDataApiSource(client, filters=({"title": "a"}, {"title": "b"})).fetch()

    assert info.value.retry_after == 2710
    assert len(seen) == 1  # no retry, and no second filter attempted
