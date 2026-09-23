"""An employer's own career site: sitemap, scope on the URL, JobPosting per page."""

import argparse
import json
import tomllib
from pathlib import Path

import httpx
from fetch_vacancies import fetch_into  # scripts/, on the path via conftest.py

from joblens.sources.careersite import CareerSiteSource, page_key, title_from
from joblens.sources.http import new_client
from joblens.sources.report import SearchRun
from joblens.sources.scope import Scope
from joblens.sources.sightings import Sightings
from joblens.sources.store import VacancyStore

ROOT = Path(__file__).parent.parent
SCOPE = Scope.from_config(
    tomllib.loads((ROOT / "sources.toml").read_text(encoding="utf-8"))["scope"]
)
SITE = "https://werkenbij.example.nl"
TEXT = "<p>Je bouwt services in Python voor ons team in Delft. " * 6 + "</p>"


def urlset(*paths: str) -> str:
    urls = "".join(f"<url><loc>{SITE}{p}</loc></url>" for p in paths)
    return (
        '<?xml version="1.0"?><urlset '
        f'xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">{urls}</urlset>'
    )


def index(*paths: str) -> str:
    maps = "".join(f"<sitemap><loc>{SITE}{p}</loc></sitemap>" for p in paths)
    return (
        '<?xml version="1.0"?><sitemapindex '
        f'xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">{maps}</sitemapindex>'
    )


def job_page(title: str, place: str = "Delft") -> str:
    posting = {
        "@context": "https://schema.org",
        "@type": "JobPosting",
        "title": title,
        "description": TEXT,
        "datePosted": "2026-09-10",
        "hiringOrganization": {"name": "Voorbeeld BV"},
        "jobLocation": {"address": {"addressLocality": place, "addressCountry": "NL"}},
    }
    return (
        f'<html><script type="application/ld+json">{json.dumps(posting)}</script>'
        "<body>...</body></html>"
    )


def site(files: dict[str, httpx.Response]):
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return files.get(request.url.path, httpx.Response(404))

    return new_client(transport=httpx.MockTransport(handler)), seen


def xml(body: str) -> httpx.Response:
    return httpx.Response(200, content=body.encode())


def html(body: str) -> httpx.Response:
    return httpx.Response(200, text=body, headers={"content-type": "text/html"})


def source(client, known=frozenset(), sitemap="/sitemap.xml") -> CareerSiteSource:
    return CareerSiteSource(
        "werkenbij.example.nl",
        f"{SITE}{sitemap}",
        "/vacatures/",
        client,
        scope=SCOPE,
        known_keys=known,
    )


def test_the_names_of_a_page():
    url = f"{SITE}/vacatures/senior-python-developer-1234/"

    assert (
        page_key(url) == "werkenbij.example.nl/vacatures/senior-python-developer-1234"
    )
    assert title_from(url) == "senior python developer"
    # corporate URLs end in ids (careers.ing.com, 2026-09-23)
    ing = "https://careers.ing.com/en/job/amsterdam/senior-data-scientist/3121/44835"
    assert title_from(ing) == "senior data scientist"


def test_a_worldwide_site_fetches_only_pages_whose_url_names_a_chosen_place():
    """careers.ing.com lists 703 pages, Manila to Bucharest to Amsterdam."""
    client, seen = site(
        {
            "/sitemap.xml": xml(
                urlset(
                    "/en/job/amsterdam/senior-data-scientist/3121/1",
                    "/en/job/makati-city/senior-data-scientist/3121/2",
                    "/en/job/eindhoven/python-developer/3121/3",
                    "/en/job/software-engineer/3121/4",
                )
            ),
            "/en/job/amsterdam/senior-data-scientist/3121/1": html(
                job_page("Senior Data Scientist", "Amsterdam")
            ),
        }
    )
    reader = CareerSiteSource(
        "careers.ing.com",
        f"{SITE}/sitemap.xml",
        "/job/",
        client,
        scope=SCOPE,
        place_in_url=True,
    )

    vacancies = reader.fetch()

    assert [v.title for v in vacancies] == ["Senior Data Scientist"]
    assert [r.url.path for r in seen if "job" in r.url.path] == [
        "/en/job/amsterdam/senior-data-scientist/3121/1"
    ]
    assert reader.stats.out_of_scope == 3


def test_only_vacancy_pages_in_scope_and_new_are_fetched():
    client, seen = site(
        {
            "/sitemap.xml": xml(
                urlset(
                    "/over-ons",
                    "/vacatures/python-developer",
                    "/vacatures/verpleegkundige-nacht",
                    "/vacatures/data-engineer",
                    "/vacatures/java-developer",
                )
            ),
            "/vacatures/python-developer": html(job_page("Python Developer")),
            "/vacatures/java-developer": html(job_page("Java Developer")),
        }
    )
    known = {"careersite:werkenbij.example.nl/vacatures/data-engineer"}
    reader = source(client, known)

    vacancies = reader.fetch()

    assert [v.title for v in vacancies] == ["Python Developer", "Java Developer"]
    pages = [r.url.path for r in seen if r.url.path != "/sitemap.xml"]
    assert pages == ["/vacatures/python-developer", "/vacatures/java-developer"]
    assert (reader.stats.listed, reader.stats.out_of_scope) == (4, 1)
    assert reader.stats.skipped_known == 1
    assert len(reader.stats.listed_keys) == 4  # what closes jobs: all of them
    assert (
        vacancies[0].key == "careersite:werkenbij.example.nl/vacatures/python-developer"
    )


def test_a_sitemap_index_is_followed_to_its_jobs():
    client, _ = site(
        {
            "/sitemap.xml": xml(index("/sitemap-pages.xml", "/sitemap-vacatures.xml")),
            "/sitemap-vacatures.xml": xml(urlset("/vacatures/python-developer")),
            "/vacatures/python-developer": html(job_page("Python Developer")),
        }
    )

    assert source(client).listing() == [f"{SITE}/vacatures/python-developer"]


def test_a_sitemap_in_googles_old_namespace_is_read():
    """werkenbijipsedebruggen.nl, 2026-09-23: sitemap/0.84, from 2005."""
    old = urlset("/vacatures/python-developer").replace(
        "http://www.sitemaps.org/schemas/sitemap/0.9",
        "http://www.google.com/schemas/sitemap/0.84",
    )
    client, _ = site({"/sitemap.xml": xml(old)})

    assert source(client).listing() == [f"{SITE}/vacatures/python-developer"]


def test_a_redirected_sitemap_is_followed():
    """careers.ing.com, 2026-09-23: /sitemap.xml answers 301 to /en/sitemap.xml."""
    moved = httpx.Response(301, headers={"location": f"{SITE}/en/sitemap.xml"})
    client, seen = site(
        {
            "/sitemap.xml": moved,
            "/en/sitemap.xml": xml(urlset("/vacatures/python-developer")),
        }
    )

    assert source(client).listing() == [f"{SITE}/vacatures/python-developer"]
    assert all(r.extensions.get("joblens_crawl") for r in seen)  # both hops


def test_a_posting_that_names_no_country_is_dutch_here():
    """insightfirst.nl's JobPostings name no place at all; the site is Dutch."""
    placeless = job_page("Data Engineer").replace(
        ', "jobLocation": {"address": {"addressLocality": "Delft", '
        '"addressCountry": "NL"}}',
        "",
    )
    client, _ = site(
        {
            "/sitemap.xml": xml(urlset("/vacatures/data-engineer")),
            "/vacatures/data-engineer": html(placeless),
        }
    )

    vacancy = source(client).fetch()[0]

    assert vacancy.country == "NL"
    assert vacancy.city is None


def test_a_page_read_and_left_out_is_not_read_again_until_the_scope_changes(
    tmp_path,
):
    """wolfgroep.nl, 2026-09-23: 102 requests a night, most of them the same
    pages read and left out again. Now once, until the scope changes."""
    client, seen = site(
        {
            "/sitemap.xml": xml(urlset("/vacatures/python-developer")),
            "/vacatures/python-developer": html(
                job_page("Python Developer", "Eindhoven")
            ),
        }
    )
    store, sightings = VacancyStore(tmp_path), Sightings()
    args = argparse.Namespace(limit=100, all_countries=False)

    def night(scope: Scope) -> SearchRun:
        known = store.existing_keys("careersite") | sightings.passed_over(
            "careersite", scope.fingerprint
        )
        reader = source(client, known)
        reader.scope = scope
        run = SearchRun("careersite", "werkenbij.example.nl")
        fetch_into(run, reader, None, {}, args, store, [], scope, sightings=sightings)
        return run

    def pages() -> int:
        return sum(1 for r in seen if r.url.path.startswith("/vacatures/"))

    night(SCOPE)  # read: the job is in Eindhoven, outside the four provinces
    assert pages() == 1
    night(SCOPE)  # the same scope: not read again
    assert pages() == 1
    wider = Scope(
        [*SCOPE.chosen, "Noord-Brabant"], SCOPE.roles, SCOPE.not_roles, SCOPE.places
    )
    run = night(wider)  # a wider scope: read again, and now it fits
    assert pages() == 2
    assert run.stored == 1


def test_every_request_is_a_crawl():
    client, seen = site(
        {
            "/sitemap.xml": xml(urlset("/vacatures/python-developer")),
            "/vacatures/python-developer": html(job_page("Python Developer")),
        }
    )

    source(client).fetch()

    assert seen and all(r.extensions.get("joblens_crawl") for r in seen)


def test_a_page_without_a_posting_or_gone_is_dropped():
    client, _ = site(
        {
            "/sitemap.xml": xml(
                urlset("/vacatures/python-developer", "/vacatures/java-developer")
            ),
            "/vacatures/python-developer": html("<html><body>Vacature</body></html>"),
        }
    )
    reader = source(client)

    assert reader.fetch() == []
    assert reader.stats.dropped_invalid == 2
