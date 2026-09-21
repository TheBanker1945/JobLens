"""CV extraction, and the generic loop underneath it, with a fake model."""

import json

import pytest
from conftest import FakeClient

from joblens.cv.extract import extract_cv
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
