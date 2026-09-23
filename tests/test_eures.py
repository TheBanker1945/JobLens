"""EURES through a fake API: the record shapes measured on 2026-09-23."""

import argparse
import json
import tomllib
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest
from fetch_vacancies import fetch_into  # scripts/, on the path via conftest.py

from joblens.sources.eures import (
    PAGE,
    SEARCH,
    EuresSource,
    nuts_codes,
    province_of,
)
from joblens.sources.http import new_client
from joblens.sources.polite import Rules
from joblens.sources.report import SearchRun
from joblens.sources.scope import Scope
from joblens.sources.store import VacancyStore

ROOT = Path(__file__).parent.parent
CONFIG = tomllib.loads((ROOT / "sources.toml").read_text(encoding="utf-8"))
SCOPE = Scope.from_config(CONFIG["scope"])
NOW = datetime(2026, 9, 23, 3, 0, tzinfo=UTC)
BODY = "<p>Je bouwt services in Python en SQL voor het team.</p>" * 6
REGIONS = ["nl32", "nl34", "nl35", "nl36"]


def record(n: int, title: str = "Python Developer", **fields) -> dict:
    """A search result as EURES sends it: no contact persons, a text summary."""
    return {
        "id": f"ID{n}",
        "title": title,
        "description": BODY,
        "employer": {"name": "Coolblue"},
        "availableLanguages": ["nl"],
        "creationDate": int((NOW - timedelta(days=3)).timestamp() * 1000),
        "locationMap": {"NL": ["NL366"]},
        "translations": {"nl": {"description": BODY}},
        **fields,
    }


def eures(pages: list[list[dict]], seen: list[httpx.Request]) -> httpx.Client:
    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        page = json.loads(request.content)["page"]
        records = pages[page - 1] if page <= len(pages) else []
        return httpx.Response(200, json={"numberRecords": 999, "jvs": records})

    return new_client(transport=httpx.MockTransport(handler))


def source(client, **options) -> EuresSource:
    return EuresSource(
        client, keyword="developer", locations=REGIONS, now=NOW, **options
    )


# --- the regions: NUTS 2024 --------------------------------------------------------


def test_the_scope_becomes_nuts_2024_codes():
    """nl33 answered 0 records on 2026-09-23: Zuid-Holland is NL36 since NUTS
    2024, Utrecht NL35. The old codes fail silently, so the mapping is tested."""
    assert nuts_codes(SCOPE.chosen) == ["nl32", "nl34", "nl35", "nl36"]
    assert province_of("NL350") == "Utrecht"  # IJsselstein
    assert province_of("NL366") == "Zuid-Holland"  # Rotterdam
    assert province_of(None) is None


def test_a_province_without_a_code_is_refused():
    with pytest.raises(ValueError, match="Noord Holland"):
        nuts_codes(["Noord Holland"])


# --- the request --------------------------------------------------------------------


def test_one_search_is_one_post_in_the_title_and_the_regions():
    seen: list[httpx.Request] = []

    source(eures([[record(1)]], seen)).fetch()

    assert len(seen) == 1
    assert seen[0].method == "POST" and str(seen[0].url) == SEARCH
    body = json.loads(seen[0].content)
    assert body["keywords"] == [{"keyword": "developer", "specificSearchCode": "TITLE"}]
    assert body["locationCodes"] == REGIONS
    assert body["sortSearch"] == "MOST_RECENT"
    assert body["publicationPeriod"] == "LAST_WEEK"
    assert body["resultsPerPage"] == PAGE


def test_no_detail_request_is_ever_made():
    """The detail adds contact persons and nothing else: the text is the same
    2,000-character summary. So a fetch is searches only."""
    seen: list[httpx.Request] = []

    source(eures([[record(n) for n in range(PAGE)], [record(99)]], seen)).fetch()

    assert {(r.method, str(r.url)) for r in seen} == {("POST", SEARCH)}


def test_pages_are_read_until_a_short_one_and_the_limit_caps_them():
    seen: list[httpx.Request] = []
    full = [record(n, f"Python Developer {n}") for n in range(PAGE)]

    found = source(eures([full, full, full], seen), pages=5).fetch(limit=60)

    assert len(seen) == 2  # 60 needs two pages of 50
    assert len(found) == 60


# --- what is kept --------------------------------------------------------------------


def test_only_dutch_and_english_records_are_kept():
    """34 of 50 records on one Dutch page were German cross-border ones."""
    seen: list[httpx.Request] = []
    records = [
        record(1, availableLanguages=["nl"]),
        record(2, availableLanguages=["en"]),
        record(3, availableLanguages=["de"]),
        record(4, availableLanguages=[]),  # unknown: kept
    ]
    eures_source = source(eures([records], seen))

    kept = eures_source.fetch()

    assert [v.source_id for v in kept] == ["ID1", "ID2", "ID4"]
    assert eures_source.stats.dropped_language == 1


def test_the_creation_date_decides_the_age():
    """LAST_WEEK is the last change: records created in June came back."""
    seen: list[httpx.Request] = []
    old = int((NOW - timedelta(days=61)).timestamp() * 1000)
    eures_source = source(eures([[record(1), record(2, creationDate=old)]], seen))

    kept = eures_source.fetch()

    assert [v.source_id for v in kept] == ["ID1"]
    assert kept[0].posted_at == NOW - timedelta(days=3)
    assert eures_source.stats.dropped_old == 1
    assert eures_source.stats.dropped_invalid == 1


def test_a_record_without_a_real_text_is_dropped():
    seen: list[httpx.Request] = []
    eures_source = source(eures([[record(1, description="<p>Bel ons.</p>")]], seen))

    assert eures_source.fetch() == []
    assert eures_source.stats.dropped_no_text == 1


def test_the_region_is_written_as_a_province():
    seen: list[httpx.Request] = []

    vacancy = source(eures([[record(1)]], seen)).fetch()[0]

    assert vacancy.raw["location"] == "Zuid-Holland, Netherlands"
    assert vacancy.country == "NL"
    assert vacancy.url.endswith("/jv-details/ID1?lang=en")
    assert SCOPE.keep(vacancy)


def test_contact_details_never_reach_the_store():
    seen: list[httpx.Request] = []
    leaky = record(
        1,
        description=BODY + "<p>Mail jan@werk.nl of bel 06-12345678</p>",
        personContacts=[{"familyName": "Jansen", "communications": {}}],
    )

    vacancy = source(eures([[leaky]], seen)).fetch()[0]

    stored = vacancy.model_dump_json()
    assert "jan@werk.nl" not in stored and "12345678" not in stored
    assert "Jansen" not in stored
    assert "[email removed]" in vacancy.text
    assert "description" not in vacancy.raw  # the text is kept once, redacted
    assert "translations" not in vacancy.raw


# --- in a fetch --------------------------------------------------------------------


def test_the_scope_still_reads_the_title(tmp_path):
    """EURES filters the region; the kind of work is the scope's to judge."""
    seen: list[httpx.Request] = []
    client = eures([[record(1), record(2, "Business Developer")]], seen)
    run = SearchRun("eures", "developer")
    args = argparse.Namespace(limit=100, all_countries=False)

    fetch_into(run, source(client), None, {}, args, VacancyStore(tmp_path), [], SCOPE)

    assert (run.listed, run.dutch, run.out_of_scope, run.stored) == (2, 2, 1, 1)
    assert run.left_out == ["Business Developer -- not the work: 'business developer'"]


def test_europa_eu_gets_its_crawl_delay():
    rules = Rules.from_config(CONFIG["politeness"])

    assert rules.delay("europa.eu") == 10
    assert rules.budget("europa.eu") == 20
