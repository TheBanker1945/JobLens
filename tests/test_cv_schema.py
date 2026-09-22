"""The two things the CV schema works out for itself: how long someone has been
working, and whether they clear an education requirement. Both are arithmetic
kept away from the model, so both are testable without one."""

from datetime import date

import pytest

from joblens.cv.schema import CVProfile, Education, Experience, meets_level
from joblens.extraction.schema import EducationLevel

TODAY = date(2026, 9, 21)


def job(**stated) -> Experience:
    blank = {
        "title": "Analist",
        "company": None,
        "start": None,
        "end": None,
        "current": False,
        "summary": None,
        "skills": [],
    }
    return Experience(**blank | stated)


def study(**stated) -> Education:
    blank = {
        "programme": "Bedrijfskunde",
        "level": None,
        "institution": None,
        "end_year": None,
        "finished": True,
    }
    return Education(**blank | stated)


def profile(*, jobs=(), studies=(), skills=()) -> CVProfile:
    return CVProfile(
        headline="Data-analist",
        summary=None,
        city=None,
        experience=list(jobs),
        education=list(studies),
        skills=list(skills),
        certificates=[],
        languages=["Dutch"],
        desired_work_mode=None,
        availability=None,
    )


def test_a_job_that_is_still_running_counts_up_to_today():
    one = profile(jobs=[job(start="2024-09", current=True)])

    assert one.years_of_experience(TODAY) == 2.0


def test_two_jobs_at_the_same_time_are_one_stretch_of_experience():
    """Four years, not five: the year they overlap is lived once."""
    overlapping = profile(
        jobs=[job(start="2018-01", end="2021-12"), job(start="2019-01", end="2019-12")]
    )

    assert overlapping.years_of_experience(TODAY) == 4.0


def test_a_year_without_a_month_counts_as_that_whole_year():
    assert profile(jobs=[job(start="2023", end="2023")]).years_of_experience(TODAY) == 1


def test_a_job_with_no_dates_at_all_cannot_be_counted():
    assert profile(jobs=[job()]).years_of_experience(TODAY) is None


def test_a_job_with_no_end_and_no_heden_is_left_out():
    """ "2019 - " with nothing after it says neither ongoing nor finished, and
    guessing which would invent experience."""
    mixed = profile(jobs=[job(start="2019"), job(start="2024-01", end="2024-12")])

    assert mixed.years_of_experience(TODAY) == 1.0


def test_a_future_end_date_does_not_buy_experience():
    """A job running until 2030 has still only been worked up to today: January
    through August, since the month we are in is not over."""
    future = profile(jobs=[job(start="2026-01", end="2030")])

    assert future.years_of_experience(TODAY) == round(8 / 12, 1)


@pytest.mark.parametrize(
    ("levels", "required", "expected"),
    [
        (["hbo"], EducationLevel.HBO, True),
        (["hbo"], EducationLevel.WO, False),
        (["wo"], EducationLevel.HBO, True),
        (["phd"], EducationLevel.WO, True),
        (["mbo", "vwo"], EducationLevel.MBO, True),
        ([], EducationLevel.MBO, False),  # nothing stated does not clear a bar
        ([], None, True),  # ... but no bar is cleared by anything
        (["vmbo"], None, True),
    ],
)
def test_education_requirements(levels, required, expected):
    one = profile(studies=[study(level=level) for level in levels])

    assert meets_level(required, one) is expected


def test_the_highest_level_wins_whatever_order_the_cv_lists_them_in():
    one = profile(studies=[study(level="wo"), study(level="mbo"), study(level="havo")])

    assert one.highest_level() == "wo"


def test_an_unfinished_study_does_not_meet_its_level():
    """Wrong in the person's favour is the worse direction (extract.py)."""
    one = profile(
        studies=[study(level="hbo", finished=False), study(level="havo", finished=True)]
    )

    assert one.highest_level() == "havo"
    assert meets_level(EducationLevel.HBO, one) is False


def test_a_study_whose_outcome_is_not_stated_still_counts():
    one = profile(studies=[study(level="hbo", finished=None)])

    assert meets_level(EducationLevel.HBO, one) is True


def test_skills_are_collected_from_the_whole_cv_keeping_its_spelling():
    one = profile(
        skills=["Power BI", "SQL"],
        jobs=[job(skills=["power bi", "dbt"])],
    )

    assert one.all_skills() == ["Power BI", "SQL", "dbt"]


def test_a_date_the_model_did_not_normalise_is_rejected():
    """The repair loop only works if a bad date fails validation first."""
    with pytest.raises(ValueError, match="start"):
        job(start="maart 2021")
