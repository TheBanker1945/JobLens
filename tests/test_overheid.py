"""werkenbijdeoverheid.nl through its sitemap: the shapes measured 2026-09-23."""

import tomllib
from pathlib import Path

import httpx

from joblens.sources.http import new_client
from joblens.sources.overheid import OverheidSource, dutch_date
from joblens.sources.scope import Scope

ROOT = Path(__file__).parent.parent
SCOPE = Scope.from_config(
    tomllib.loads((ROOT / "sources.toml").read_text(encoding="utf-8"))["scope"]
)
BASE = "https://www.werkenbijdeoverheid.nl/vacatures"
SITEMAP = f"""<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url><loc>{BASE}/senior-python-engineer-DEF2660-2008-5412</loc>
       <lastmod>2026-09-22T17:39:49Z</lastmod></url>
  <url><loc>{BASE}/cateringmedewerker-b-DEF2610-2035-8818</loc></url>
  <url><loc>{BASE}/senior-java-developer-DICT-2026-9401</loc></url>
  <url><loc>{BASE}/data-engineer-CVZ-2026-5930</loc></url>
  <url><loc>{BASE}/about-us</loc></url>
</urlset>"""
SECTION = "<p>Je bouwt services in Python voor de krijgsmacht. " * 8 + "</p>"
PAGE = f"""<html><body>
<script>window.dataLayer.push({{
'Rijksorganisatie': 'Ministerie van Defensie',
'Functienaam': 'Senior Python Engineer',
'Standplaats': 'Utrecht',
'Startdatum': '4 september 2026',
'Einddatum': '25 september 2026'
}});</script>
<nav>Op deze pagina: Dit ga je doen</nav>
<section id="dit_ga_je_doen_anchor"><h2>Dit ga je doen</h2>
  <div class="s-article-content">{SECTION}</div></section>
<section id="dit_vragen_wij_anchor"><h2>Dit vragen wij</h2>
  <div class="s-article-content"><ul><li>Python</li></ul>
  <p>Vragen? Mail recruitment@mindef.nl of bel 06-12345678</p></div></section>
<footer>Werken bij de overheid</footer>
</body></html>"""


def government(pages: dict[str, httpx.Response] | None = None):
    """A fake werkenbijdeoverheid.nl; every request it saw, with its extensions."""
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.path.endswith("sitemap-vacatures.xml"):
            return httpx.Response(200, content=SITEMAP.encode())
        found = (pages or {}).get(request.url.path.rsplit("/", 1)[-1])
        return found or httpx.Response(200, text=PAGE)

    return new_client(transport=httpx.MockTransport(handler)), seen


def test_the_sitemap_is_a_list_of_ids_and_titles():
    client, _ = government()

    listed = OverheidSource(client).listing()

    assert [(item.vacancy_id, item.title) for item in listed] == [
        ("DEF2660-2008-5412", "senior python engineer"),
        ("DEF2610-2035-8818", "cateringmedewerker b"),
        ("DICT-2026-9401", "senior java developer"),
        ("CVZ-2026-5930", "data engineer"),
    ]


def test_only_new_pages_whose_title_is_the_work_are_asked_for():
    client, seen = government()
    source = OverheidSource(client, scope=SCOPE, known_keys={"overheid:CVZ-2026-5930"})

    vacancies = source.fetch()

    pages = [r.url.path.rsplit("/", 1)[-1] for r in seen if "sitemap" not in r.url.path]
    assert pages == [
        "senior-python-engineer-DEF2660-2008-5412",
        "senior-java-developer-DICT-2026-9401",
    ]
    assert len(vacancies) == 2
    assert (source.stats.listed, source.stats.out_of_scope) == (4, 1)
    assert source.stats.skipped_known == 1
    # the sitemap is complete: every listed id counts as still open
    assert len(source.stats.listed_keys) == 4


def test_every_request_is_a_crawl():
    """So the gate checks robots.txt for each (sources/polite.py)."""
    client, seen = government()

    OverheidSource(client, scope=SCOPE).fetch()

    assert seen and all(r.extensions.get("joblens_crawl") for r in seen)


def test_a_page_becomes_a_vacancy():
    client, _ = government()

    vacancy = OverheidSource(client, scope=SCOPE).fetch()[0]

    assert vacancy.key == "overheid:DEF2660-2008-5412"
    assert vacancy.title == "Senior Python Engineer"
    assert vacancy.company == "Ministerie van Defensie"
    assert vacancy.city == "Utrecht"
    assert vacancy.posted_at.date().isoformat() == "2026-09-04"
    assert vacancy.raw["Einddatum"] == "25 september 2026"
    assert vacancy.text.startswith("Dit ga je doen")
    assert "Dit vragen wij" in vacancy.text
    assert "Op deze pagina" not in vacancy.text  # only the sections
    assert "recruitment@mindef.nl" not in vacancy.text
    assert "06-12345678" not in vacancy.text


def test_a_page_that_went_since_the_sitemap_is_dropped():
    client, _ = government(
        {"senior-java-developer-DICT-2026-9401": httpx.Response(404)}
    )
    source = OverheidSource(client, scope=SCOPE)

    vacancies = source.fetch()

    assert [v.source_id for v in vacancies] == ["DEF2660-2008-5412", "CVZ-2026-5930"]
    assert source.stats.dropped_invalid == 1


def test_a_page_with_no_text_is_dropped():
    empty = httpx.Response(200, text="<html><body>Onderhoud</body></html>")
    client, _ = government({"data-engineer-CVZ-2026-5930": empty})
    source = OverheidSource(client, scope=SCOPE)

    source.fetch()

    assert source.stats.dropped_no_text == 1


def test_dutch_dates():
    assert dutch_date("4 september 2026").isoformat() == "2026-09-04T00:00:00"
    assert dutch_date("31 februari 2026") is None
    assert dutch_date("") is None
