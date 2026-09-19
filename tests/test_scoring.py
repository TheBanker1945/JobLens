import pytest

from joblens.evals.scoring import (
    normalize,
    score_details,
    score_list,
    score_scalar,
)
from joblens.extraction.schema import VacancyDetails

BASE = {
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
    "skills": ["SQL", "Excel", "Power BI"],
    "languages_required": ["Dutch"],
    "contract_type": "temporary",
}


def details(**changes):
    return VacancyDetails.model_validate(BASE | changes)


@pytest.mark.parametrize(
    ("expected", "predicted", "outcome"),
    [
        ("Utrecht", "Utrecht", "correct"),
        (None, None, "correct_null"),
        ("Utrecht", "Amsterdam", "wrong"),
        ("Utrecht", None, "missed"),
        (None, "Utrecht", "hallucinated"),
    ],
)
def test_five_scalar_outcomes(expected, predicted, outcome):
    assert score_scalar([expected], predicted) == outcome


def test_alternatives_are_accepted():
    accepted = ["Bakkerij De Vries B.V.", "Bakkerij De Vries"]

    assert score_scalar(accepted, "Bakkerij De Vries") == "correct"
    assert score_scalar(accepted, "De Vries") == "wrong"


def test_presence_only_ignores_wording_but_not_hallucination():
    assert score_scalar(["schaal 11"], "cao schaal 11", presence_only=True) == "correct"
    assert score_scalar([None], "marktconform", presence_only=True) == "hallucinated"


@pytest.mark.parametrize(
    ("a", "b"),
    [
        (3200, 3200.0),
        (" Utrecht ", "utrecht"),
        ("Power  BI.", "power bi"),
        (14.85, 14.850),
    ],
)
def test_normalize_ignores_formatting(a, b):
    assert normalize(a) == normalize(b)


def test_list_scores_partial_credit():
    score = score_list("skills", ["SQL", "Excel", "Power BI"], ["sql", "Python"])

    assert score.precision == 0.5  # 1 of 2 predicted is right
    assert score.recall == pytest.approx(1 / 3)  # 1 of 3 expected found
    assert score.f1 == pytest.approx(0.4)
    assert score.missing == ["excel", "power bi"]
    assert score.extra == ["python"]


@pytest.mark.parametrize(
    ("expected", "predicted", "f1"),
    [([], [], 1.0), ([], ["SQL"], 0.0), (["SQL"], [], 0.0)],
)
def test_list_edge_cases(expected, predicted, f1):
    assert score_list("skills", expected, predicted).f1 == f1


def test_score_details_counts_outcomes():
    predicted = details(
        city=None,  # missed
        salary_note="afhankelijk van ervaring",  # hallucinated
        hours_min=30,  # wrong
    )

    score = score_details(details(), predicted)

    assert score.count("missed") == 1
    assert score.count("hallucinated") == 1
    assert score.count("wrong") == 1
    assert score.accuracy == pytest.approx(10 / 13)  # 13 scalar fields
    assert {s.field for s in score.lists} == {"skills", "languages_required"}


def test_perfect_prediction_scores_one():
    score = score_details(details(), details())

    assert score.accuracy == 1.0
    assert all(s.f1 == 1.0 for s in score.lists)
