import pytest
from pydantic import ValidationError

from joblens.extraction.schema import VacancyDetails

VALID = {
    "title": "Junior Data Analist",
    "company": "Bakkerij De Vries B.V.",
    "city": "Utrecht",
    "work_mode": "hybrid",
    "hours_min": 32,
    "hours_max": 36,
    "salary_min": 3200,
    "salary_max": 3800,
    "salary_period": "month",
    "salary_note": None,
    "education_level": "hbo",
    "experience_years_min": 0,
    "skills": ["SQL", "Power BI"],
    "languages_required": ["Dutch"],
    "contract_type": "temporary",
}


def test_valid_details_parse():
    details = VacancyDetails.model_validate(VALID)

    assert details.work_mode == "hybrid"
    assert details.salary_max == 3800


def test_unknown_values_can_be_null():
    nulls = {k: None for k in VALID} | {
        "title": "Orderpicker",
        "skills": [],
        "languages_required": [],
    }

    assert VacancyDetails.model_validate(nulls).salary_min is None


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"hours_min": 40, "hours_max": 32}, "hours_min is greater"),
        ({"salary_min": 5000}, "salary_min is greater"),
        ({"salary_period": None}, "salary_period is required"),
        (
            {"salary_min": None, "salary_max": None},  # period "month" left behind
            "salary_period is set but no salary amount",
        ),
        ({"work_mode": "hybride"}, "work_mode"),  # Dutch word, not an allowed value
        ({"hours_max": 80}, "hours_max"),
        ({"salary_min": 0}, "salary_min"),  # a placeholder, not a salary
        ({"bonus": "yes"}, "bonus"),  # invented extra field
    ],
)
def test_invalid_details_are_rejected(change, message):
    with pytest.raises(ValidationError, match=message):
        VacancyDetails.model_validate(VALID | change)


def test_schema_requires_every_field_and_forbids_extras():
    # Schema-constrained decoding needs this: every field present, nothing extra.
    schema = VacancyDetails.model_json_schema()

    assert set(schema["required"]) == set(VacancyDetails.model_fields)
    assert schema["additionalProperties"] is False


def test_only_a_maximum_salary_is_fine():
    # "up to €85,000 per year": no floor, but a period is needed for the ceiling.
    details = VacancyDetails.model_validate(
        VALID | {"salary_min": None, "salary_max": 85000, "salary_period": "year"}
    )

    assert details.salary_min is None
