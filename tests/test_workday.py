"""Workday career sites: the Netherlands facet, the scope before any text, the
page ceiling that must not close jobs, robots.txt, and finding sites in links.

A fake Workday answers with the shapes measured on Rabobank on 2026-09-23.
"""

import argparse
import json
import tomllib
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest
from fetch_vacancies import fetch_into  # scripts/, on the path via conftest.py

from joblens.sources.boards import board_of
from joblens.sources.http import new_client
from joblens.sources.polite import Disallowed
from joblens.sources.report import SearchRun
from joblens.sources.scope import Scope
from joblens.sources.sightings import Sightings
from joblens.sources.store import VacancyStore
from joblens.sources.workday import (
    WorkdaySource,
    find_dutch_facet,
    parse_board,
)

ROOT = Path(__file__).parent.parent
SCOPE = Scope.from_config(
    tomllib.loads((ROOT / "sources.toml").read_text(encoding="utf-8"))["scope"]
)
NL = "1ece61cb100301ee521400047941651f"  # Rabobank's id for "The Netherlands"
FACETS = [
    {
        "facetParameter": "Country",
        "descriptor": "Country",
        "values": [
            {"descriptor": "Brazil", "id": "br", "count": 95},
            {"descriptor": "The Netherlands", "id": NL, "count": 61},
        ],
    }
]
BODY = "<p>Je bouwt services in Python en SQL voor het team. " * 6 + "</p>"
NIGHT = datetime(2026, 9, 23, 3, 0, tzinfo=UTC)


def posting(n: int, title: str, place: str = "Utrecht Croeselaan 18", nl=True):
    where, what = place.replace(" ", "-"), title.replace(" ", "-")
    return {
        "title": title,
        "externalPath": f"/job/{where}/{what}_JR_{n}",
        "locationsText": place,
        "bulletFields": [f"JR_{n}"],
        "nl": nl,
    }


class FakeWorkday:
    """rabobank-like: robots.txt, a paged listing that can filter on country,
    and one detail per posting."""

    def __init__(
        self,
        postings: list[dict],
        *,
        robots: str = "User-agent: *\nDisallow: /refreshFacet/\n",
        facets: list[dict] = FACETS,
        ceiling: int | None = None,  # offsets from here on answer empty pages
        can_apply: bool = True,
        text: str = BODY,
    ):
        self.postings, self.robots, self.facets = postings, robots, facets
        self.ceiling, self.can_apply, self.text = ceiling, can_apply, text
        self.requests: list[tuple[str, str]] = []

    def client(self) -> httpx.Client:
        return new_client(transport=httpx.MockTransport(self.handle))

    def handle(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        self.requests.append((request.method, path))
        if path == "/robots.txt":
            return httpx.Response(200, text=self.robots)
        if request.method == "POST":
            body = json.loads(request.content)
            assert body["limit"] == 20  # Workday answers a larger page with nothing
            rows = self.postings
            if body["appliedFacets"]:
                assert body["appliedFacets"] == {"Country": [NL]}
                rows = [p for p in rows if p["nl"]]
            offset = body["offset"]
            past_ceiling = self.ceiling is not None and offset >= self.ceiling
            page = [] if past_ceiling else rows[offset : offset + 20]
            listed = [{k: v for k, v in p.items() if k != "nl"} for p in page]
            return httpx.Response(
                200,
                json={"total": len(rows), "facets": self.facets, "jobPostings": listed},
            )
        external = path.split("/wday/cxs/rabobank/jobs", 1)[1]
        match = next(p for p in self.postings if p["externalPath"] == external)
        return httpx.Response(
            200,
            json={
                "hiringOrganization": {"name": "Rabobank"},
                "jobPostingInfo": {
                    "title": match["title"],
                    "jobDescription": self.text,
                    "location": match["locationsText"],
                    "startDate": "2026-09-23",
                    "country": {"descriptor": "Netherlands"},
                    "externalUrl": f"https://wd3.myworkdaysite.com/x{external}",
                    "canApply": self.can_apply,
                },
            },
        )

    def details(self) -> int:
        return sum(1 for method, path in self.requests if "/job/" in path)


def source(fake: FakeWorkday, **kwargs) -> WorkdaySource:
    return WorkdaySource("rabobank.wd3/jobs", fake.client(), scope=SCOPE, **kwargs)


# --- which site a link names ------------------------------------------------------


def test_the_ways_a_stored_link_names_a_workday_site():
    """Real links from the store, 2026-09-23; the locale is sometimes missing."""
    cases = {
        "https://rabobank.wd3.myworkdayjobs.com/en-US/jobs/job/Utrecht-Croeselaan-18/"
        "DevOps-Engineer---Store_JR_00143161": "rabobank.wd3/jobs",
        "https://salesforce.wd12.myworkdayjobs.com/en-US/External_Career_Site/job/"
        "Netherlands---Amsterdam/Forward-Deployed-Engineer-Lead_JR359478": (
            "salesforce.wd12/External_Career_Site"
        ),
        "https://eriks.wd3.myworkdayjobs.com/nl-NL/Careers/job/x/Project_R1": (
            "eriks.wd3/Careers"
        ),
        "https://Kardex.wd103.myworkdayjobs.com/kardex/job/Woerden/x_JR1": (
            "kardex.wd103/kardex"
        ),
    }
    for link, board in cases.items():
        assert board_of(link) == ("workday", board), link


def test_a_board_is_tenant_data_centre_and_site():
    assert parse_board("salesforce.wd12/External_Career_Site") == (
        "salesforce",
        "wd12",
        "External_Career_Site",
    )


def test_the_id_is_the_requisition_not_the_title():
    """A renamed title keeps its requisition id, so it stays the same job."""
    workday = WorkdaySource("rabobank.wd3/jobs", None)

    assert (
        workday.source_id("/job/Utrecht/Senior-Data-Engineer_JR_00145400-1")
        == "rabobank:JR_00145400-1"
    )


# --- the listing -----------------------------------------------------------------


def test_the_netherlands_facet_is_found_by_its_value():
    grouped = [
        {
            "facetParameter": "locationMainGroup",
            "values": [
                {
                    "facetParameter": "locationCountry",
                    "values": [{"descriptor": "Netherlands", "id": "nl-id"}],
                }
            ],
        }
    ]

    assert find_dutch_facet(FACETS) == ("Country", NL)
    assert find_dutch_facet(grouped) == ("locationCountry", "nl-id")
    assert find_dutch_facet([{"facetParameter": "timeType", "values": []}]) is None


def test_the_listing_asks_for_the_netherlands_twenty_at_a_time():
    postings = [posting(n, f"Job {n}") for n in range(45)]
    postings += [posting(n, f"Job {n}", "Sao Paulo", nl=False) for n in range(45, 60)]
    fake = FakeWorkday(postings)
    workday = source(fake)

    listed = workday.listing()

    assert len(listed) == 45
    posts = [path for method, path in fake.requests if method == "POST"]
    assert len(posts) == 4  # one to learn the facets, then 20 + 20 + 5
    assert workday.stats.complete
    assert (workday.stats.total, workday.stats.dutch) == (60, 45)


# --- the scope before the text ---------------------------------------------------


def test_only_new_jobs_in_scope_cost_a_text_request():
    fake = FakeWorkday(
        [
            posting(1, "Python Developer"),
            posting(2, "Analyst Group Steering"),  # not the work
            posting(3, "Data Engineer", "Eindhoven Fellenoord 17"),  # not the place
            posting(4, "Software Engineer"),  # already stored
        ]
    )
    workday = source(fake, known_keys={"workday:rabobank:JR_4"})

    vacancies = workday.fetch()

    assert [v.title for v in vacancies] == ["Python Developer"]
    assert fake.details() == 1
    assert (workday.stats.out_of_scope, workday.stats.skipped_known) == (2, 1)
    assert workday.stats.listed_keys == {
        f"workday:rabobank:JR_{n}" for n in (1, 2, 3, 4)
    }


def test_without_a_netherlands_facet_a_posting_must_name_a_dutch_place():
    """Otherwise a site that lists the world would cost a request per job."""
    fake = FakeWorkday(
        [posting(1, "Python Developer"), posting(2, "Python Developer", "Sao Paulo")],
        facets=[],
    )
    workday = source(fake)

    vacancies = workday.fetch()

    assert len(vacancies) == 1
    assert "no Netherlands filter" in workday.stats.left_out[0]


def test_the_text_and_its_details():
    workday = source(FakeWorkday([posting(1, "Python Developer")]))

    vacancy = workday.fetch()[0]

    assert vacancy.key == "workday:rabobank:JR_1"
    assert vacancy.company == "Rabobank"
    assert vacancy.country == "NL"
    assert vacancy.posted_at.date().isoformat() == "2026-09-23"
    assert "Python en SQL" in vacancy.text


def test_a_legal_entity_with_a_ledger_code_gives_way_to_the_tenant():
    """Philips, 2026-09-23: three entities, "NL3M Philips International BV",
    "NL9A NL Best Industrial", "NL42 CL Netherlands Reinvoicing EUR"."""
    philips = WorkdaySource("philips.wd3/jobs-and-careers", None)

    def employer(name: str) -> str:
        return philips.company({"hiringOrganization": {"name": name}})

    assert employer("NL3M Philips International BV") == "Philips"
    assert employer("NL9A NL Best Industrial") == "Philips"
    assert employer("NN Personeel B.V.") == "NN Personeel B.V."  # a real name stays
    assert employer("") == "Philips"


def test_a_job_that_no_longer_takes_applications_or_has_no_text_is_dropped():
    closed = source(FakeWorkday([posting(1, "Python Developer")], can_apply=False))
    empty = source(FakeWorkday([posting(1, "Python Developer")], text="<p>x</p>"))

    assert closed.fetch() == [] and closed.stats.dropped_invalid == 1
    assert empty.fetch() == [] and empty.stats.dropped_no_text == 1


# --- robots.txt ------------------------------------------------------------------


def test_a_site_robots_txt_disallows_is_not_read_at_all():
    """Rabobank, 2026-09-23: "Disallow: /jobs/", its whole career site."""
    fake = FakeWorkday(
        [posting(1, "Python Developer")],
        robots="User-agent: *\nDisallow: /jobs/\nDisallow: /refreshFacet/\n",
    )

    with pytest.raises(Disallowed) as info:
        source(fake).fetch()

    assert info.value.status == "disallowed"
    assert "/jobs/" in info.value.detail
    assert fake.requests == [("GET", "/robots.txt")]  # nothing else was asked


def test_an_unreadable_robots_txt_allows_nothing_and_a_missing_one_everything():
    """RFC 9309: 5xx means "assume disallowed", 4xx means "no rules"."""

    def site(status: int) -> WorkdaySource:
        def handle(request):
            if request.url.path == "/robots.txt":
                return httpx.Response(status)
            return FakeWorkday([posting(1, "Python Developer")]).handle(request)

        client = new_client(transport=httpx.MockTransport(handle))
        return WorkdaySource("rabobank.wd3/jobs", client, scope=SCOPE)

    with pytest.raises(Disallowed):
        site(503).listing()
    assert len(site(404).listing()) == 1


def test_robots_txt_is_read_once_per_host():
    fake = FakeWorkday([posting(1, "Python Developer")])
    shared: dict = {}

    source(fake, robots=shared).listing()
    source(fake, robots=shared).listing()

    assert fake.requests.count(("GET", "/robots.txt")) == 1


# --- closing jobs, and the page ceiling -------------------------------------------


def night(fake, store, sightings, when) -> SearchRun:
    run = SearchRun("workday", "rabobank.wd3/jobs")
    workday = source(fake, known_keys=store.existing_keys("workday"))
    args = argparse.Namespace(limit=100, all_countries=False)
    fetch_into(
        run, workday, None, {}, args, store, [], SCOPE, sightings=sightings, now=when
    )
    return run


def test_a_job_the_complete_listing_no_longer_holds_has_closed(tmp_path):
    store, sightings = VacancyStore(tmp_path), Sightings()
    first = [posting(1, "Python Developer"), posting(2, "Java Developer")]

    night(FakeWorkday(first), store, sightings, NIGHT)
    run = night(FakeWorkday(first[:1]), store, sightings, NIGHT + timedelta(days=1))

    assert (run.closed, run.incomplete) == (1, False)
    assert sightings.entries["workday:rabobank:JR_2"].closed_at is not None


def test_a_listing_that_stopped_short_closes_nothing(tmp_path):
    """Workday is reported to stop paging near 2,000 results; a listing cut
    off there says nothing about the jobs past the cut."""
    store, sightings = VacancyStore(tmp_path), Sightings()
    jobs = [posting(1, "Python Developer")] + [
        posting(n, f"Analyst {n}") for n in range(2, 60)
    ]
    night(FakeWorkday(jobs), store, sightings, NIGHT)

    cut = FakeWorkday(list(reversed(jobs)), ceiling=40)  # job 1 now past the cut
    run = night(cut, store, sightings, NIGHT + timedelta(days=1))

    assert run.incomplete
    assert run.closed == 0
    assert sightings.entries["workday:rabobank:JR_1"].closed_at is None


def test_a_listing_longer_than_one_run_reads_is_incomplete_too():
    fake = FakeWorkday([posting(n, f"Job {n}") for n in range(50)])
    workday = source(fake, max_postings=20)

    assert len(workday.listing()) == 20
    assert not workday.stats.complete
