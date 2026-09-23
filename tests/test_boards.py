"""Finding employer boards in stored links, the board list, and the two adapters
that discovery added: Recruitee on an employer's own domain, and SmartRecruiters.

No network: fake transports answer with the payload shapes measured 2026-09-23.
"""

import argparse
import tomllib
from pathlib import Path

import httpx
from fetch_vacancies import fetch_into  # scripts/, on the path via conftest.py

from joblens.sources.base import Vacancy
from joblens.sources.boards import (
    append_board,
    board_of,
    candidates,
    known_boards,
    load_boards,
    load_config,
)
from joblens.sources.http import new_client
from joblens.sources.recruitee import RecruiteeSource
from joblens.sources.report import SearchRun
from joblens.sources.scope import Scope
from joblens.sources.smartrecruiters import SmartRecruitersSource
from joblens.sources.store import VacancyStore

ROOT = Path(__file__).parent.parent
SCOPE = Scope.from_config(
    tomllib.loads((ROOT / "sources.toml").read_text(encoding="utf-8"))["scope"]
)


# --- which board a link names -----------------------------------------------------


def test_the_ways_the_stored_links_name_a_board():
    """Each shape below is a real stored link, shortened."""
    cases = {
        "https://deephealth.recruitee.com/o/senior-data-engineer?source=Indeed": (
            "recruitee",
            "deephealth",
        ),
        "https://lefebvresdu.recruitee.com/l/nl/o/technical-web-analyst": (
            "recruitee",
            "lefebvresdu",
        ),
        "https://job-boards.greenhouse.io/redwoodsoftware/jobs/4405791009": (
            "greenhouse",
            "redwoodsoftware",
        ),
        "https://job-boards.eu.greenhouse.io/jetbrains/jobs/4979524101": (
            "greenhouse",
            "jetbrains",
        ),
        "https://boards.greenhouse.io/adyen/jobs/1": ("greenhouse", "adyen"),
        "https://jobs.smartrecruiters.com/Varrlyn/744000151009148--sr-data-engineer": (
            "smartrecruiters",
            "Varrlyn",
        ),
        "https://grnh.se/yhp3vzer2us": ("grnh.se", "yhp3vzer2us"),
        # Recruitee under the employer's own name: a candidate, checked later.
        "https://jobs.wildflowers.dev/o/full-stack-java-developer?source=Indeed": (
            "recruitee",
            "jobs.wildflowers.dev",
        ),
    }
    for link, expected in cases.items():
        assert board_of(link) == expected, link


def test_links_that_name_no_board_we_can_read():
    assert board_of("https://boards.greenhouse.io/embed/job_app?for=x") is None
    assert (
        board_of("https://www.werkenvoornederland.nl/vacatures/data-engineer") is None
    )
    assert board_of("https://leaseweb.teamtailor.com/jobs/8431594-cloud") is None


def job(n: int, link: str, source: str = "indeed") -> Vacancy:
    return Vacancy(
        source=source,
        source_id=str(n),
        url=f"https://nl.indeed.com/viewjob?jk={n}",
        title="Python Developer",
        text="A real vacancy.",
        raw={"job_url_direct": link},
    )


def test_candidates_are_counted_and_known_boards_left_out():
    stored = [
        job(1, "https://deephealth.recruitee.com/o/a"),
        job(2, "https://deephealth.recruitee.com/o/b"),
        job(3, "https://Channable.recruitee.com/o/c"),  # already read, any case
        job(4, "https://leaseweb.teamtailor.com/jobs/8431594-cloud"),
        job(5, "https://jobs.ashbyhq.com/AeroVect/5d36"),
    ]

    found, unreadable = candidates(stored, {"recruitee": {"channable"}})

    assert [(c.platform, c.board, c.links) for c in found] == [
        ("recruitee", "deephealth", 2)
    ]
    assert unreadable == {"teamtailor": 1, "ashby": 1}


# --- the board list -----------------------------------------------------------------


def test_the_board_list_joins_the_settings(tmp_path):
    sources = tmp_path / "sources.toml"
    sources.write_text('[scope]\nprovinces = ["Utrecht"]\n', encoding="utf-8")
    boards = tmp_path / "boards.toml"
    boards.write_text('[[recruitee]]\nslug = "channable"\n', encoding="utf-8")

    config = load_config(sources, boards)

    assert config["recruitee"] == [{"slug": "channable"}]
    assert config["scope"]["provinces"] == ["Utrecht"]


def test_an_accepted_board_is_appended_with_its_numbers(tmp_path):
    boards = tmp_path / "boards.toml"
    boards.write_text('[[recruitee]]\nslug = "channable"\n', encoding="utf-8")

    append_board(boards, "smartrecruiters", "Coolblue", "306 jobs, 12 in scope")
    append_board(boards, "recruitee", "careers.twd.nl", "11 jobs, 9 in scope")

    written = load_boards(boards)
    assert written["smartrecruiters"] == [{"company": "Coolblue"}]
    assert known_boards(written)["recruitee"] == {"channable", "careers.twd.nl"}
    assert "# 306 jobs, 12 in scope" in boards.read_text(encoding="utf-8")


def test_the_committed_board_list_reads():
    config = load_config(ROOT / "sources.toml", ROOT / "boards.toml")

    assert {"slug": "channable"} in config["recruitee"]
    assert {"slug": "adyen"} in config["greenhouse"]


# --- Recruitee on the employer's own domain ------------------------------------------


def test_a_recruitee_board_on_its_own_domain():
    seen = []

    def handler(request):
        seen.append(str(request.url))
        return httpx.Response(200, json={"offers": []})

    client = new_client(transport=httpx.MockTransport(handler))
    RecruiteeSource("vacatures.coloriet.nl", client).fetch()
    RecruiteeSource("channable", client).fetch()

    assert seen == [
        "https://vacatures.coloriet.nl/api/offers/",
        "https://channable.recruitee.com/api/offers/",
    ]


# --- SmartRecruiters: the scope before the text -----------------------------------


def posting(n: int, title: str, place: str = "Amsterdam, NH, Netherlands") -> dict:
    return {"id": str(n), "name": title, "location": {"fullLocation": place}}


def detail(n: int, title: str, active: bool = True) -> dict:
    return {
        "id": str(n),
        "name": title,
        "active": active,
        "company": {"name": "Coolblue"},
        "location": {"city": "Rotterdam", "country": "nl"},
        "releasedDate": "2026-09-23T07:56:24.915Z",
        "postingUrl": f"https://jobs.smartrecruiters.com/Coolblue/{n}-job",
        "jobAd": {
            "sections": {
                "jobDescription": {
                    "title": "Vacatureomschrijving",
                    # a real text runs to thousands; under 200 is dropped as broken
                    "text": "<p>Je bouwt services in Python voor het team. " * 6
                    + "Mail jan@coolblue.nl</p>",
                },
                "qualifications": {
                    "title": "Functie-eisen",
                    "text": "<ul><li>SQL</li></ul>",
                },
            }
        },
    }


def smartrecruiters(listing: list[dict], details: dict[str, dict], seen: list):
    """A fake API: the listing in pages of 100, and one detail per posting."""

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.path)
        parts = request.url.path.rstrip("/").split("/")
        if parts[-1] == "postings":
            offset = int(request.url.params["offset"])
            page = listing[offset : offset + 100]
            return httpx.Response(
                200, json={"totalFound": len(listing), "content": page}
            )
        return httpx.Response(200, json=details[parts[-1]])

    return new_client(transport=httpx.MockTransport(handler))


def test_only_new_jobs_in_scope_cost_a_text_request():
    listing = [
        posting(1, "Python Developer"),
        posting(2, "Customer Service Medewerker"),  # not the work
        posting(3, "Data Engineer", "Eindhoven, NB, Netherlands"),  # not the place
        posting(4, "Software Engineer"),  # already stored
        posting(5, "Backend Developer"),
    ]
    details = {"1": detail(1, "Python Developer"), "5": detail(5, "Backend Developer")}
    seen: list[str] = []
    source = SmartRecruitersSource(
        "Coolblue",
        smartrecruiters(listing, details, seen),
        scope=SCOPE,
        known_keys={"smartrecruiters:4"},
    )

    vacancies = source.fetch()

    assert [v.title for v in vacancies] == ["Python Developer", "Backend Developer"]
    detail_requests = [path for path in seen if not path.endswith("/postings")]
    assert len(detail_requests) == 2
    assert (source.stats.listed, source.stats.out_of_scope) == (5, 2)
    assert source.stats.skipped_known == 1
    assert source.stats.left_out[0].startswith("Customer Service Medewerker -- ")


def test_the_listing_is_read_page_by_page():
    listing = [posting(n, "Verpleegkundige") for n in range(150)]
    seen: list[str] = []

    postings = SmartRecruitersSource(
        "Coolblue", smartrecruiters(listing, {}, seen)
    ).listing()

    assert len(postings) == 150
    assert len(seen) == 2  # 100, then 50


def test_the_text_is_its_sections_and_contact_details_go():
    seen: list[str] = []
    client = smartrecruiters(
        [posting(1, "Python Developer")], {"1": detail(1, "x")}, seen
    )

    vacancy = SmartRecruitersSource("Coolblue", client, scope=SCOPE).fetch()[0]

    assert "Vacatureomschrijving" in vacancy.text
    assert "SQL" in vacancy.text
    assert "jan@coolblue.nl" not in vacancy.text
    assert vacancy.url == "https://jobs.smartrecruiters.com/Coolblue/1-job"
    assert vacancy.country == "NL"
    assert vacancy.posted_at.year == 2026


def test_a_posting_that_closed_in_between_is_dropped():
    seen: list[str] = []
    client = smartrecruiters(
        [posting(1, "Python Developer")], {"1": detail(1, "x", active=False)}, seen
    )
    source = SmartRecruitersSource("Coolblue", client, scope=SCOPE)

    assert source.fetch() == []
    assert source.stats.dropped_invalid == 1


def test_an_event_posted_as_a_job_is_dropped():
    """Deloitte, 2026-09-23: "Engineering, AI & Data Kookworkshop", 131 chars."""
    workshop = detail(1, "Engineering, AI & Data Kookworkshop")
    workshop["jobAd"]["sections"] = {
        "companyDescription": {"title": "Bedrijfsomschrijving", "text": "Bij ons."},
        "additionalInformation": {"title": "Aanvullende informatie", "text": "x"},
    }
    seen: list[str] = []
    client = smartrecruiters(
        [posting(1, "Engineering, AI & Data Kookworkshop")], {"1": workshop}, seen
    )
    source = SmartRecruitersSource("DeloitteNetherlands", client, scope=SCOPE)

    assert source.fetch() == []
    assert source.stats.dropped_no_text == 1


def test_the_run_report_adds_up_when_a_source_filters_before_fetching(tmp_path):
    """Jobs the source never asked the text of are still Dutch, and still out
    of scope or known: "fits" (dutch - out of scope) must not go negative."""
    listing = [posting(1, "Python Developer")] + [
        posting(n, "Verpleegkundige") for n in range(2, 12)
    ]
    seen: list[str] = []
    source = SmartRecruitersSource(
        "Coolblue",
        smartrecruiters(listing, {"1": detail(1, "Python Developer")}, seen),
        scope=SCOPE,
    )
    run = SearchRun("smartrecruiters", "Coolblue")
    args = argparse.Namespace(limit=100, all_countries=False)

    assert fetch_into(
        run, source, _no_gate(), {}, args, VacancyStore(tmp_path), [], SCOPE
    )

    assert (run.listed, run.dutch, run.out_of_scope, run.stored) == (11, 11, 10, 1)
    assert run.dutch - run.out_of_scope == 1
    assert len(run.left_out) == 10


def _no_gate():
    """fetch_into only asks the gate for the scraped sources."""

    class Unused:
        def __getattr__(self, name):
            raise AssertionError(f"the gate was asked: {name}")

    return Unused()
