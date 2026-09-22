"""CV extraction, and the generic loop underneath it, with a fake model."""

import json

import pytest
from conftest import FakeClient

from joblens.cv.extract import extract_cv, verify_dates
from joblens.cv.schema import CVProfile
from joblens.llm.structured import StructuredError

VALID = {
    "headline": "Data-analist",
    "summary": None,
    "city": "Amsterdam",
    "experience": [
        {
            "title": "Data-analist",
            "company": "Coolblue",
            "start": "2023-09",
            "end": None,
            "current": True,
            "summary": "Dashboards voor het team Retentie.",
            "skills": ["Python", "Power BI"],
        }
    ],
    "education": [
        {
            "programme": "Bedrijfskunde",
            "level": "hbo",
            "institution": "Hogeschool Utrecht",
            "end_year": 2022,
            "finished": True,
        }
    ],
    "skills": ["SQL"],
    "certificates": ["Rijbewijs B"],
    "languages": ["Dutch", "English"],
    "desired_work_mode": "hybrid",
    "availability": "32-40 uur",
}
CV_TEXT = "Lisa de Vries\nData-analist bij Coolblue sinds september 2023..."


def test_a_valid_reply_becomes_a_profile():
    result = extract_cv(CV_TEXT, FakeClient(json.dumps(VALID)))

    assert result.details.headline == "Data-analist"
    assert result.details.experience[0].current is True
    assert result.details.years_of_experience() is not None
    assert result.attempts == 1


def test_the_cv_schema_is_what_the_model_is_asked_for():
    client = FakeClient(json.dumps(VALID))

    extract_cv(CV_TEXT, client, mode="schema")

    sent = client.calls[0]["format"]["json_schema"]
    assert sent["name"] == "CVProfile"
    assert set(sent["schema"]["properties"]) == set(CVProfile.model_fields)


def test_the_cv_is_the_only_thing_in_the_user_message():
    client = FakeClient(json.dumps(VALID))

    extract_cv(CV_TEXT, client)

    assert client.calls[0]["messages"][1] == {"role": "user", "content": CV_TEXT}


def test_a_date_the_model_wrote_out_in_words_is_sent_back_for_repair():
    """The date format is enforced by the schema, so "maart 2021" fails
    validation and the model is told exactly which field was wrong."""
    wrong = json.loads(json.dumps(VALID))
    wrong["experience"][0]["start"] = "maart 2021"
    client = FakeClient(json.dumps(wrong), json.dumps(VALID))

    result = extract_cv(CV_TEXT, client)

    assert result.attempts == 2
    repair = client.calls[1]["messages"][-1]["content"]
    assert "experience.0.start" in repair


def test_an_invented_field_is_refused():
    """extra="forbid" is what stops a model adding "seniority": "senior" and the
    rest of the app quietly believing it."""
    client = FakeClient(json.dumps(VALID | {"seniority": "senior"}), json.dumps(VALID))

    assert extract_cv(CV_TEXT, client).attempts == 2


def test_it_gives_up_rather_than_returning_half_a_profile():
    client = FakeClient("no json here", "still none")

    with pytest.raises(StructuredError, match="No valid CVProfile"):
        extract_cv(CV_TEXT, client, max_attempts=2)


# --- the dates, checked against the CV rather than believed -----------------
#
# A real CV whose font had eaten every year produced "2024-12 - 2025-09" with no
# hedge, and two of its five periods were wrong. These are the years a model
# invents; `verify_dates` asks the text instead.

CV_TEXT = """Jan Bakker
Backend Developer Dec ???? ? Sep ????
Van Dijk Software
Stagiair Backend, februari 2019 tot juni 2019
MBO Niveau 4 Software Developer, diploma behaald in 2026
"""


def profile_with(experience, education=()) -> CVProfile:
    return CVProfile.model_validate(
        VALID | {"experience": list(experience), "education": list(education)}
    )


def job(**stated) -> dict:
    return {
        "title": "Backend Developer",
        "company": "Van Dijk Software",
        "start": None,
        "end": None,
        "current": False,
        "summary": None,
        "skills": [],
    } | stated


def study(**stated) -> dict:
    return {
        "programme": "Software Developer",
        "level": "mbo",
        "institution": "ROC",
        "end_year": None,
        "finished": True,
    } | stated


def test_a_year_that_is_not_in_the_cv_becomes_null():
    profile = profile_with([job(start="2024-12", end="2025-09")])

    checked = verify_dates(profile, CV_TEXT)

    assert checked.profile.experience[0].start is None
    assert checked.profile.experience[0].end is None
    assert [(d.field, d.value) for d in checked.dropped] == [
        ("start", "2024-12"),
        ("end", "2025-09"),
    ]
    assert checked.dates == 2
    assert checked.faithfulness == 0.0


def test_a_year_the_cv_really_states_is_kept():
    profile = profile_with([job(start="2019-02", end="2019-06")])

    checked = verify_dates(profile, CV_TEXT)

    assert checked.profile.experience[0].start == "2019-02"
    assert checked.profile.experience[0].end == "2019-06"
    assert checked.dropped == []
    assert checked.faithfulness == 1.0


def test_an_education_year_is_checked_the_same_way():
    profile = profile_with([], [study(end_year=2026), study(end_year=2021)])

    checked = verify_dates(profile, CV_TEXT)

    assert checked.profile.education[0].end_year == 2026
    assert checked.profile.education[1].end_year is None
    assert [d.field for d in checked.dropped] == ["end_year"]


def test_a_year_written_short_on_the_cv_counts_as_stated():
    """ "'19 - '23" is the CV copying its own years; expanding them is reading,
    not inventing."""
    profile = profile_with([job(start="2019-03", end="2023-01")])

    checked = verify_dates(profile, "Backend Developer, '19 - '23")

    assert checked.dropped == []


def test_the_experience_total_stops_being_a_number_when_the_dates_go():
    """The worst part of the invented dates was not the dates: it was "1.1 years
    covered by listed jobs" printed as a fact underneath them."""
    profile = profile_with([job(start="2024-12", end="2025-09")])

    assert profile.years_of_experience() == 0.8
    assert verify_dates(profile, CV_TEXT).profile.years_of_experience() is None


def test_nothing_else_about_the_profile_is_touched():
    profile = profile_with([job(start="2024-12", skills=["Python"])])

    checked = verify_dates(profile, CV_TEXT)

    assert checked.profile.headline == profile.headline
    assert checked.profile.experience[0].skills == ["Python"]
    assert checked.profile.experience[0].title == "Backend Developer"
