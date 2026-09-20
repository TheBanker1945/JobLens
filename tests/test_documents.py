import pytest

from joblens.embeddings.documents import build_document
from joblens.extraction.schema import VacancyDetails

DETAILS = VacancyDetails.model_validate(
    {
        "title": "Medior Backend Developer",
        "company": "Fietsdeel",
        "city": "Amsterdam",
        "work_mode": "hybrid",
        "hours_min": 40,
        "hours_max": 40,
        "salary_min": None,
        "salary_max": None,
        "salary_period": None,
        "salary_note": "marktconform",
        "education_level": "hbo",
        "experience_years_min": 3,
        "skills": ["Python", "Django", "Kubernetes"],
        "languages_required": ["English"],
        "contract_type": "permanent",
    }
)


def test_raw_returns_the_text_unchanged():
    assert build_document("Vacature tekst", DETAILS, "raw") == "Vacature tekst"


def test_title_only():
    assert build_document("x", DETAILS, "title_only") == "Medior Backend Developer"


def test_structured_summarises_the_fields():
    doc = build_document("x", DETAILS, "structured")

    assert doc.startswith("Medior Backend Developer")
    assert "Plaats: Amsterdam" in doc
    assert "Vaardigheden: Python, Django, Kubernetes" in doc
    assert "Uren per week: 40" in doc  # one value, not "40-40"
    assert "Salaris: marktconform" in doc  # no amounts: fall back to the note
    assert "Werkervaring: 3 jaar" in doc


def test_structured_leaves_out_empty_fields():
    empty = DETAILS.model_copy(update={"city": None, "skills": [], "company": None})

    doc = build_document("x", empty, "structured")

    assert "Plaats" not in doc
    assert "Vaardigheden" not in doc
    assert "Bedrijf" not in doc


def test_salary_range_is_readable():
    paid = DETAILS.model_copy(
        update={"salary_min": 3200.0, "salary_max": 3800.0, "salary_period": "month"}
    )

    assert "Salaris: 3200-3800 per month" in build_document("x", paid, "structured")


def test_structured_without_details_is_an_error():
    with pytest.raises(ValueError, match="needs extracted details"):
        build_document("x", None, "structured")


def test_unknown_style_is_an_error():
    with pytest.raises(ValueError, match="unknown style"):
        build_document("x", DETAILS, "summary")
