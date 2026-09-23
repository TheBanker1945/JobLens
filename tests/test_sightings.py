"""Closed vacancies: a board that stops listing a job, a search job that ages out,
and the two places that must not be fooled -- a board that failed or came back
empty, and the evals, which keep reading everything."""

import argparse
import json
from datetime import UTC, datetime, timedelta

import httpx
from fetch_vacancies import fetch_into  # scripts/, on the path via conftest.py

from joblens.corpus import load_corpus
from joblens.sources.base import Vacancy
from joblens.sources.http import new_client
from joblens.sources.recruitee import RecruiteeSource
from joblens.sources.report import SearchRun
from joblens.sources.sightings import SEARCH_MAX_AGE_DAYS, SEARCH_SEEN_DAYS, Sightings
from joblens.sources.store import VacancyStore

NIGHT = datetime(2026, 9, 23, 3, 0, tzinfo=UTC)
ARGS = argparse.Namespace(limit=100, all_countries=True)
BODY = "Je bouwt services in Python en SQL voor het team. " * 6


# --- the rules ---------------------------------------------------------------------


def test_a_job_a_board_stops_listing_has_closed():
    sightings = Sightings()
    sightings.seen({"recruitee:1", "recruitee:2"}, "channable", NIGHT)

    closed = sightings.close_missing(
        "channable", "recruitee", {"recruitee:1"}, NIGHT + timedelta(days=1)
    )

    assert closed == 1
    assert sightings.entries["recruitee:2"].closed_at == NIGHT + timedelta(days=1)
    assert sightings.entries["recruitee:1"].closed_at is None


def test_an_empty_board_closes_nothing():
    """A board that suddenly lists nothing is more likely broken than emptied."""
    sightings = Sightings()
    sightings.seen({"recruitee:1", "recruitee:2"}, "channable", NIGHT)

    assert sightings.close_missing("channable", "recruitee", set(), NIGHT) == 0


def test_only_that_boards_jobs_can_close():
    sightings = Sightings()
    sightings.seen({"recruitee:1"}, "channable", NIGHT)
    sightings.seen({"recruitee:9"}, "nmbrs", NIGHT)
    sightings.seen({"greenhouse:5"}, "channable", NIGHT)  # same name, other source

    sightings.close_missing("channable", "recruitee", {"recruitee:1"}, NIGHT)

    assert all(entry.closed_at is None for entry in sightings.entries.values())


def test_a_job_listed_again_reopens():
    sightings = Sightings()
    sightings.seen({"recruitee:1", "recruitee:2"}, "channable", NIGHT)
    sightings.close_missing("channable", "recruitee", {"recruitee:1"}, NIGHT)

    reopened = sightings.seen({"recruitee:1", "recruitee:2"}, "channable", NIGHT)

    assert reopened == 1
    assert sightings.entries["recruitee:2"].closed_at is None


def test_jobs_stored_before_sightings_began_close_when_every_board_answered():
    """They have no board on record, so only all boards together can say."""
    sightings = Sightings()
    sightings.seen({"recruitee:1"}, "channable", NIGHT)

    closed = sightings.close_unseen(
        "recruitee", {"recruitee:1", "recruitee:old"}, NIGHT
    )

    assert closed == 1
    assert sightings.entries["recruitee:old"].closed_at == NIGHT


def test_no_board_answering_closes_nothing():
    sightings = Sightings()

    assert sightings.close_unseen("recruitee", {"recruitee:old"}, NIGHT) == 0


def job(source: str, n: int, posted: datetime | None = None) -> Vacancy:
    return Vacancy(
        source=source,
        source_id=str(n),
        url=f"https://example.test/{n}",
        # distinct titles: the store drops the same title with the same text
        title=f"Python Developer {n}",
        posted_at=posted,
        fetched_at=NIGHT - timedelta(days=1),
        text=BODY,
    )


def test_a_search_job_no_search_lists_any_more_closes_by_age():
    """A job missing from tonight's Indeed search may just be older than the
    search window, so absence proves nothing; without a recent sighting, age
    is what is left."""
    sightings = Sightings()
    young = job("indeed", 1, NIGHT - timedelta(days=SEARCH_MAX_AGE_DAYS - 1))
    old = job("indeed", 2, NIGHT - timedelta(days=SEARCH_MAX_AGE_DAYS + 1))
    undated = job("jobdataapi", 3)  # no posted date: fetched yesterday counts

    assert sightings.is_open(young, NIGHT)
    assert not sightings.is_open(old, NIGHT)
    assert sightings.is_open(undated, NIGHT)


def test_a_search_job_a_search_still_lists_is_open_however_old():
    """Indeed, 2026-09-23: a last-7-days search returned Wildflowers jobs whose
    date_posted was 222 days back. Still listed means still advertised."""
    sightings = Sightings()
    reposted = job("indeed", 1, NIGHT - timedelta(days=222))
    sightings.seen({"indeed:1"}, None, NIGHT - timedelta(days=2))

    assert sightings.is_open(reposted, NIGHT)
    assert not sightings.is_open(reposted, NIGHT + timedelta(days=SEARCH_SEEN_DAYS))


def test_a_board_job_is_open_until_a_listing_says_otherwise():
    sightings = Sightings()
    ancient = job("greenhouse", 1, NIGHT - timedelta(days=400))  # still listed

    assert sightings.is_open(ancient, NIGHT)  # boards are never judged by age


def test_the_file_round_trips(tmp_path):
    path = tmp_path / "sightings.json"
    sightings = Sightings.load(path)
    sightings.seen({"recruitee:1", "recruitee:2"}, "channable", NIGHT)
    sightings.close_missing("channable", "recruitee", {"recruitee:1"}, NIGHT)
    sightings.save()

    again = Sightings.load(path)

    assert again.entries == sightings.entries
    assert not (tmp_path / "sightings.tmp").exists()  # moved into place


# --- through a real fetch ------------------------------------------------------------


def board(*ids: int, status: int = 200) -> httpx.Client:
    offers = [
        {"id": n, "title": f"Python Developer {n}", "description": f"<p>{BODY}</p>"}
        for n in ids
    ]
    return new_client(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(status, json={"offers": offers})
        )
    )


def night(client, store, sightings, when) -> SearchRun:
    run = SearchRun("recruitee", "channable")
    fetch_into(
        run,
        RecruiteeSource("channable", client),
        None,
        {},
        ARGS,
        store,
        [],
        sightings=sightings,
        now=when,
    )
    return run


def test_three_nights_of_one_board(tmp_path):
    store, sightings = VacancyStore(tmp_path), Sightings()

    first = night(board(1, 2), store, sightings, NIGHT)
    second = night(board(1), store, sightings, NIGHT + timedelta(days=1))
    third = night(board(1, 2), store, sightings, NIGHT + timedelta(days=2))

    assert (first.stored, first.closed) == (2, 0)
    assert (second.closed, second.reopened) == (1, 0)
    assert (third.closed, third.reopened) == (0, 1)


def test_a_job_its_board_still_lists_stays_open_as_a_stored_indeed_copy(tmp_path):
    """Wildflowers, 2026-09-23: the store kept the Indeed copy (it came first)
    and dropped the board's own listing as a duplicate. The Indeed copy is 222
    days old by its date, so without the board's word it would close."""
    store, sightings = VacancyStore(tmp_path), Sightings()
    indeed_copy = Vacancy(
        source="indeed",
        source_id="in-1",
        url="https://nl.indeed.com/viewjob?jk=1",
        title="Python Developer 1",
        posted_at=NIGHT - timedelta(days=222),
        text=BODY,
    )
    store.add([indeed_copy])

    night(board(1), store, sightings, NIGHT)  # the board lists the same job

    assert sightings.is_open(indeed_copy, NIGHT + timedelta(days=1))


def test_a_copy_elsewhere_does_not_reopen_what_its_own_board_closed():
    sightings = Sightings()
    sightings.seen({"recruitee:1"}, "channable", NIGHT)
    sightings.close_missing("channable", "recruitee", {"recruitee:2"}, NIGHT)

    sightings.seen_elsewhere({"recruitee:1"}, NIGHT + timedelta(days=1))

    assert sightings.entries["recruitee:1"].closed_at == NIGHT


def test_a_board_that_failed_closes_nothing(tmp_path):
    store, sightings = VacancyStore(tmp_path), Sightings()
    night(board(1, 2), store, sightings, NIGHT)

    failed = night(board(status=500), store, sightings, NIGHT + timedelta(days=1))

    assert failed.status == "failed"
    assert all(entry.closed_at is None for entry in sightings.entries.values())


# --- who sees closed vacancies ------------------------------------------------------


def test_matching_leaves_closed_jobs_out_and_the_evals_do_not(tmp_path):
    raw = tmp_path / "data" / "raw"
    store = VacancyStore(raw / "vacancies")
    store.add([job("recruitee", 1), job("recruitee", 2)])
    sightings = Sightings.load(raw / "sightings.json")
    sightings.seen({"recruitee:1", "recruitee:2"}, "channable", NIGHT)
    sightings.close_missing("channable", "recruitee", {"recruitee:1"}, NIGHT)
    sightings.save()

    for_matching = load_corpus("raw", tmp_path, open_only=True)
    for_evals = load_corpus("raw", tmp_path)

    assert [v.key for v in for_matching.vacancies] == ["recruitee:1"]
    assert for_matching.funnel.closed == 1
    assert "1 closed" in for_matching.funnel.line()
    assert len(for_evals.vacancies) == 2
    assert for_evals.funnel.closed == 0


def test_the_sightings_file_is_json_a_person_can_read(tmp_path):
    path = tmp_path / "sightings.json"
    sightings = Sightings.load(path)
    sightings.seen({"recruitee:1"}, "channable", NIGHT)
    sightings.save()

    written = json.loads(path.read_text(encoding="utf-8"))

    assert written["recruitee:1"] == {
        "first_seen": "2026-09-23T03:00:00+00:00",
        "last_seen": "2026-09-23T03:00:00+00:00",
        "board": "channable",
        "passed_over": None,
        "closed_at": None,
    }
