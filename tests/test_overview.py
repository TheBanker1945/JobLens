"""The sources as the owner's admin page shows them (7.10.1): stored, open and
in JobLens per source and per board, each source's last fetch -- the last one
that asked it -- and the sites that are refusing us."""

from datetime import UTC, datetime, timedelta

import pytest
from conftest import details

from joblens.extraction.store import DetailsStore, ExtractedVacancy
from joblens.sources.base import Vacancy
from joblens.sources.overview import Row, overview
from joblens.sources.polite import FetchState, SiteState
from joblens.sources.report import RunReport, SearchRun
from joblens.sources.sightings import Sighting, Sightings
from joblens.sources.store import VacancyStore

NOW = datetime(2026, 9, 30, 12, 0, tzinfo=UTC)
CONFIG = {
    "greenhouse": [{"slug": "adyen"}, {"slug": "catawiki"}, {"slug": "newboard"}],
    "indeed": {"enabled": True},
    "jobdataapi": {"enabled": False},
}


def job(source: str, source_id: str, title: str = "Developer", **fields) -> Vacancy:
    return Vacancy(
        source=source,
        source_id=source_id,
        url=f"https://example.test/{source}/{source_id}",
        title=title,
        company=fields.pop("company", f"Company {source_id}"),
        city="Utrecht",
        text=f"Je bouwt API's in Python, bij {source} {source_id}.",
        fetched_at=NOW - timedelta(days=2),
        **fields,
    )


def build(root):
    """Greenhouse: adyen has one open job and one closed, catawiki one open
    that was never extracted, and one job is from before boards were written
    down. Indeed: one job, one copy of adyen's, one open application. A
    jobdataapi job from when that source was still on."""
    raw = root / "data" / "raw"
    open_adyen = job("greenhouse", "1", company="Adyen")
    vacancies = {
        "greenhouse": [
            open_adyen,
            job("greenhouse", "2"),
            job("greenhouse", "3"),
            job("greenhouse", "4"),
        ],
        "indeed": [
            job("indeed", "a"),
            job("indeed", "b", company="Adyen"),  # greenhouse:1: title, firm, place
            job("indeed", "c", title="Open sollicitatie"),
        ],
        "jobdataapi": [job("jobdataapi", "x")],
    }
    store = VacancyStore(raw / "vacancies")
    for source, jobs in vacancies.items():
        # Written directly: the store's own duplicate check would refuse the copy.
        store.directory.mkdir(parents=True, exist_ok=True)
        store.path_for(source).write_text(
            "".join(v.model_dump_json() + "\n" for v in jobs), encoding="utf-8"
        )
    extracted = DetailsStore(raw / "extracted")
    for source, keys in {
        "greenhouse": ["greenhouse:1", "greenhouse:2", "greenhouse:4"],
        "indeed": ["indeed:a", "indeed:b"],
        "jobdataapi": ["jobdataapi:x"],
    }.items():
        extracted.add(
            source,
            [ExtractedVacancy(key=k, model="t", details=details("Dev")) for k in keys],
        )

    sightings = Sightings(raw / "sightings.json")
    seen = NOW - timedelta(hours=9)
    sightings.entries = {
        "greenhouse:1": Sighting(seen, seen, "adyen"),
        "greenhouse:2": Sighting(seen, seen, "adyen", closed_at=seen),
        "greenhouse:3": Sighting(seen, seen, "catawiki"),
        "greenhouse:4": Sighting(seen, seen, None, closed_at=seen),
    }
    sightings.save()

    older = RunReport(started_at=NOW - timedelta(days=1))
    older.add(
        SearchRun(
            "greenhouse",
            "adyen",
            listed=40,
            dutch=12,
            out_of_scope=10,
            known=1,
            stored=1,
            closed=1,
        )
    )
    older.add(
        SearchRun("greenhouse", "catawiki", listed=9, dutch=3, out_of_scope=2, stored=1)
    )
    older.add(SearchRun("indeed", "python in Utrecht", listed=5))
    older.write(raw / "runs")
    # A run of Indeed alone, later: it says nothing about Greenhouse.
    newer = RunReport(started_at=NOW - timedelta(hours=2))
    newer.add(
        SearchRun(
            "indeed", "python in Utrecht", listed=7, dutch=7, stored=2, duplicate=1
        )
    )
    newer.add(
        SearchRun(
            "indeed",
            "java in Leiden",
            status="throttled",
            detail="descriptions stopped arriving",
        )
    )
    newer.write(raw / "runs")

    state = FetchState(raw / "fetch-state.json")
    state.sites = {
        "indeed.com": SiteState(2, NOW + timedelta(hours=20), "429", NOW),
        "old.example": SiteState(1, NOW - timedelta(hours=1), "403", NOW),
    }
    state.save()
    return root


@pytest.fixture
def seen(tmp_path):
    return overview(build(tmp_path), CONFIG, now=NOW)


def by_source(seen):
    return {one.source: one for one in seen.sources}


def test_each_source_counts_stored_open_and_what_joblens_ranks(seen):
    sources = by_source(seen)
    greenhouse, indeed = sources["greenhouse"], sources["indeed"]

    # closed jobs are not open; a job never extracted is open but not ranked
    assert (greenhouse.stored, greenhouse.open, greenhouse.in_joblens) == (4, 2, 1)
    # an open application is no job; a copy of a Greenhouse job is ranked once
    assert (indeed.stored, indeed.open, indeed.in_joblens) == (3, 2, 1)
    assert [one.source for one in seen.sources] == [
        "greenhouse",
        "indeed",
        "jobdataapi",
    ]  # switched off last, its vacancies still counted
    assert sources["jobdataapi"].enabled is False
    assert sources["jobdataapi"].in_joblens == 1


def test_a_board_has_every_number_and_the_boards_toml_order(seen):
    rows = by_source(seen)["greenhouse"].rows

    assert [row.name for row in rows] == ["adyen", "catawiki", "newboard", None]
    adyen, catawiki, new, unknown = rows
    assert (adyen.stored, adyen.open, adyen.in_joblens) == (2, 1, 1)
    assert (adyen.run.listed, adyen.run.in_scope, adyen.run.new) == (40, 2, 1)
    assert adyen.run.closed == 1
    assert (catawiki.stored, catawiki.open, catawiki.in_joblens) == (1, 1, 0)
    assert new == Row(name="newboard", stored=0, open=0, in_joblens=0, run=None)
    # stored before sightings named a board: counted, on a row of its own
    assert (unknown.stored, unknown.open, unknown.in_joblens) == (1, 0, 0)


def test_a_source_shows_the_last_fetch_that_asked_it(seen):
    """A run of Indeed alone is Indeed's last fetch, not Greenhouse's."""
    greenhouse, indeed = by_source(seen)["greenhouse"], by_source(seen)["indeed"]

    assert greenhouse.fetched_at == NOW - timedelta(days=1)
    assert indeed.fetched_at == NOW - timedelta(hours=2)
    assert greenhouse.run.listed == 49 and greenhouse.run.status == "ok"
    assert (indeed.run.listed, indeed.run.new, indeed.run.duplicate) == (7, 2, 1)
    assert (indeed.run.status, indeed.run.broken) == ("broken", 1)
    assert indeed.problems == [
        "indeed (java in Leiden): throttled - descriptions stopped arriving"
    ]
    assert greenhouse.problems == []


def test_a_search_has_only_its_last_run(seen):
    """A vacancy two searches found belongs to neither: no stock per search."""
    rows = by_source(seen)["indeed"].rows

    assert [row.name for row in rows] == ["python in Utrecht", "java in Leiden"]
    assert all(row.stored is None and row.in_joblens is None for row in rows)
    assert rows[1].run.status == "throttled"
    assert rows[1].run.detail == "descriptions stopped arriving"


def test_only_sites_still_refusing_us_are_listed(seen):
    assert [(r.site, r.reason, r.strikes) for r in seen.refusing] == [
        ("indeed.com", "429", 2)
    ]
    assert seen.refusing[0].until == NOW + timedelta(hours=20)


def test_an_empty_raw_folder_is_an_empty_overview(tmp_path):
    empty = overview(tmp_path, {}, now=NOW)

    assert empty.sources == [] and empty.refusing == []
