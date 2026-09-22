"""The five ways a CV can ask the index a question."""

import pytest

from joblens.cv.documents import build_queries, profile_summary
from joblens.cv.schema import CVProfile, Education, Experience

CV_TEXT = "Lisa de Vries\n\nData-analist bij Coolblue.\n\n" + "werk. " * 400


def profile(**stated) -> CVProfile:
    blank = {
        "headline": "Data-analist",
        "summary": None,
        "city": "Amsterdam",
        "experience": [],
        "education": [],
        "skills": ["SQL", "Power BI"],
        "certificates": ["Rijbewijs B"],
        "languages": ["Dutch"],
        "desired_work_mode": None,
        "availability": None,
    }
    return CVProfile(**blank | stated)


def job(title: str, **stated) -> Experience:
    blank = {
        "title": title,
        "company": "Coolblue",
        "start": "2022-01",
        "end": None,
        "current": True,
        "summary": "Dashboards.",
        "skills": ["dbt"],
    }
    return Experience(**blank | stated)


def test_raw_sends_the_cv_as_one_question():
    parts = build_queries("raw", text=CV_TEXT)

    assert len(parts) == 1
    assert parts[0].text == CV_TEXT


def test_the_profile_uses_the_same_labels_a_vacancy_is_indexed_with():
    """Both sides of the comparison say "Vaardigheden", so the embedding does not
    have to see through two different words for the same thing."""
    summary = profile_summary(profile(experience=[job("Data-analist")]))

    assert summary.startswith("Data-analist")
    assert "Plaats: Amsterdam" in summary
    assert "Vaardigheden: SQL, Power BI, dbt" in summary
    assert "Certificaten: Rijbewijs B" in summary


def test_the_profile_leaves_out_what_the_cv_did_not_say():
    summary = profile_summary(profile())

    assert "Werkervaring" not in summary  # no dated jobs: no number to state
    assert "Beschikbaarheid" not in summary


def test_roles_asks_once_per_job_plus_once_for_the_background():
    parts = build_queries(
        "roles",
        text=CV_TEXT,
        profile=profile(
            experience=[job("Data-analist"), job("Junior data-analist")],
            education=[
                Education(
                    programme="Bedrijfskunde",
                    level="hbo",
                    institution=None,
                    end_year=2022,
                    finished=True,
                )
            ],
        ),
    )

    assert [part.label for part in parts] == [
        "job: Data-analist @ Coolblue",
        "job: Junior data-analist @ Coolblue",
        "education and skills",
    ]
    assert "Opleiding: Bedrijfskunde" in parts[-1].text


def test_a_cv_with_no_jobs_still_asks_something():
    """Someone who has just graduated has no experience section, and an empty
    list of queries would rank nothing at all."""
    parts = build_queries("roles", text=CV_TEXT, profile=profile())

    assert len(parts) == 1
    assert "Vaardigheden" in parts[0].text


def test_chunks_cover_the_whole_cv_and_say_which_piece_they_are():
    parts = build_queries("chunks", text=CV_TEXT)

    assert len(parts) > 1
    assert parts[0].label == f"part 1 of {len(parts)}"


def test_the_wishlist_has_to_be_written_first():
    with pytest.raises(ValueError, match="needs a written advert"):
        build_queries("wishlist", text=CV_TEXT)

    parts = build_queries("wishlist", text=CV_TEXT, wishlist="Gezocht: data-analist")
    assert parts[0].text == "Gezocht: data-analist"


@pytest.mark.parametrize("style", ["profile", "roles"])
def test_the_styles_built_from_fields_need_the_fields(style):
    with pytest.raises(ValueError, match="needs an extracted profile"):
        build_queries(style, text=CV_TEXT)


def test_an_unknown_style_is_refused():
    with pytest.raises(ValueError, match="unknown CV style"):
        build_queries("vibes", text=CV_TEXT)


def test_no_advert_is_written_for_an_empty_profile():
    """Handed nothing, a model writes "there is no CV" -- and that got embedded."""
    from conftest import FakeClient

    from joblens.cv.schema import CVProfile
    from joblens.cv.wishlist import write_ideal_vacancy

    empty = CVProfile(
        headline="",
        summary=None,
        city=None,
        experience=[],
        education=[],
        skills=[],
        certificates=[],
        languages=[],
        desired_work_mode=None,
        availability=None,
    )
    client = FakeClient("Er is geen cv of profieltekst meegegeven")

    with pytest.raises(ValueError, match="empty"):
        write_ideal_vacancy(empty, client)
    assert client.calls == []
