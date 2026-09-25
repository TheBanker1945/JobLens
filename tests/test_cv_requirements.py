"""The requirement judge: two questions to a model, and the adding up in code.

The arithmetic is the part a person can check by hand, so most of these tests
are about `score`: what each kind of requirement is worth, and which answers
decide the verdict on their own.
"""

import json
import threading

import pytest
from conftest import FakeClient
from pydantic import ValidationError

from joblens.cv.documents import QueryPart
from joblens.cv.judge import Verdict
from joblens.cv.match import CVMatch
from joblens.cv.requirements import (
    Kind,
    Listed,
    Requirement,
    RequirementBook,
    RequirementJudgement,
    Status,
    VacancyRequirements,
    Work,
    check_requirements,
    combine,
    judge_by_requirements,
    numbered,
    score,
)
from joblens.cv.store import CVCache
from joblens.sources.base import Vacancy

CV = """Junior full-stack developer
Ik bouwde webapps met TypeScript en Python.
Stage bij een bureau, 1 jaar."""

VACANCY_TEXT = """Wat je meebrengt:
- Je hebt minimaal 3 jaar ervaring als full-stack developer
- Je hebt kennis van C#, Python en TypeScript
- Hbo werk- en denkniveau
- Ervaring met Azure is mooi meegenomen"""

VACANCY = Vacancy(
    source="indeed",
    source_id="7",
    url="https://example.test",
    title="Full-stack Developer",
    company="Van der Valk",
    city="Utrecht",
    text=VACANCY_TEXT,
)
MATCH = CVMatch(VACANCY, 0.03, "document", QueryPart("the whole CV", CV))


def requirement(name, kind=Kind.SKILL, *, must=True, knockout=False, quote=None):
    return Requirement(
        name=name,
        quote=quote or name,
        kind=kind,
        must=must,
        knockout=knockout,
    )


PYTHON = requirement("Python", quote="Python")
CSHARP = requirement("C#", quote="C#")
YEARS = requirement(
    "3 jaar ervaring",
    Kind.EXPERIENCE,
    quote="minimaal 3 jaar ervaring als full-stack developer",
)
AZURE = requirement("Azure", must=False, quote="Ervaring met Azure")


def test_everything_met_in_your_own_line_of_work_is_strong_and_full():
    assert score([PYTHON, CSHARP], [Status.MET, Status.MET], Work.SAME) == (
        Verdict.STRONG,
        100,
    )


def test_a_missing_knockout_is_weak_however_well_the_rest_fits():
    big = requirement("BIG-registratie", Kind.ELIGIBILITY, knockout=True)
    verdict, fit = score([PYTHON, big], [Status.MET, Status.MISSING], Work.SAME)

    assert verdict is Verdict.WEAK
    assert fit <= 39


def test_different_work_is_weak_whatever_the_words_share():
    verdict, fit = score([PYTHON], [Status.MET], Work.DIFFERENT)

    assert verdict is Verdict.WEAK
    assert fit <= 39


def test_years_short_costs_half_what_a_missing_skill_costs():
    """The stretch rule, as arithmetic: 6.3's whole reason to exist."""
    _, years_short = score([PYTHON, YEARS], [Status.MET, Status.MISSING], Work.SAME)
    _, skill_short = score([PYTHON, CSHARP], [Status.MET, Status.MISSING], Work.SAME)

    assert years_short > skill_short
    assert years_short == round(100 * (0.7 * (1 / 1.5) + 0.3))
    assert skill_short == round(100 * (0.7 * 0.5 + 0.3))


def test_a_nice_to_have_counts_a_quarter():
    _, fit = score([PYTHON, AZURE], [Status.MET, Status.MISSING], Work.SAME)
    assert fit == round(100 * (0.7 * (1 / 1.25) + 0.3))


def test_travel_or_location_is_not_a_skill_to_miss():
    travel = requirement("25% reizen", Kind.ELIGIBILITY)
    assert score([PYTHON, travel], [Status.MET, Status.MISSING], Work.SAME)[1] == 100


def test_partly_is_half():
    _, fit = score([PYTHON, CSHARP], [Status.MET, Status.PARTLY], Work.SAME)
    assert fit == round(100 * (0.7 * 0.75 + 0.3))


def test_a_vacancy_that_asks_nothing_is_judged_on_the_work_alone():
    assert score([], [], Work.NEXT) == (Verdict.POSSIBLE, 60)


def test_a_knockout_cannot_be_a_nice_to_have():
    with pytest.raises(ValidationError, match="nice-to-have"):
        VacancyRequirements(
            requirements=[
                requirement("Duits", Kind.LANGUAGE, must=False, knockout=True)
            ]
        )


def test_a_requirement_the_vacancy_does_not_contain_is_dropped():
    found = VacancyRequirements(
        requirements=[
            requirement("C#", quote="kennis van C#, Python en TypeScript"),
            requirement("Kubernetes", quote="Je werkt met Kubernetes"),
        ]
    )
    listed = check_requirements(found, VACANCY_TEXT)

    assert [one.name for one in listed.requirements] == ["C#"]
    assert [one.name for one in listed.dropped] == ["Kubernetes"]


def test_the_list_says_must_or_nice_and_quotes_the_vacancy():
    text = numbered([PYTHON, AZURE])
    assert text.splitlines() == [
        '1. Python (must, skill): "Python"',
        '2. Azure (nice to have, skill): "Ervaring met Azure"',
    ]


def answer(*answers, work="same", conflicts=()) -> RequirementJudgement:
    return RequirementJudgement.model_validate(
        {
            "answers": [
                {"requirement": n, "cv_quote": q, "status": s} for n, q, s in answers
            ],
            "conflicts": [
                {"wish": w, "cv_quote": c, "vacancy_quote": v} for w, c, v in conflicts
            ],
            "work_quote": "Junior full-stack developer",
            "work": work,
            "summary": "Je bouwt al webapps; C# ontbreekt.",
        }
    )


def test_an_answer_whose_quote_is_not_in_the_cv_counts_as_missing():
    """In 3.6 a verdict survived losing its quote; here the number moves."""
    listed = Listed([PYTHON, CSHARP], [])
    honest = combine(
        answer((1, "TypeScript en Python", "met"), (2, "", "missing")),
        listed,
        CV,
        MATCH,
    )
    invented = combine(
        answer(
            (1, "TypeScript en Python", "met"), (2, "5 jaar C# bij Microsoft", "met")
        ),
        listed,
        CV,
        MATCH,
    )

    assert invented.judgement.fit == honest.judgement.fit
    assert invented.dropped[0].quote == "5 jaar C# bij Microsoft"
    assert [gap.requirement for gap in invented.judgement.gaps] == ["C#"]


def test_a_requirement_the_model_skipped_is_missing_not_met():
    judged = combine(
        answer((1, "TypeScript en Python", "met")),
        Listed([PYTHON, CSHARP], []),
        CV,
        MATCH,
    )
    assert [gap.requirement for gap in judged.judgement.gaps] == ["C#"]


def test_a_partly_is_both_evidence_and_a_gap():
    judged = combine(
        answer((1, "Stage bij een bureau, 1 jaar", "partly")),
        Listed([YEARS], []),
        CV,
        MATCH,
    )
    assert judged.judgement.evidence[0].cv_quote == "Stage bij een bureau, 1 jaar"
    assert judged.judgement.gaps[0].requirement == "3 jaar ervaring (partly shown)"


def requirements_reply(*found: Requirement) -> str:
    return VacancyRequirements(requirements=list(found)).model_dump_json()


def test_a_vacancy_is_read_once_however_many_threads_ask(tmp_path):
    client = FakeClient(requirements_reply(PYTHON))
    book = RequirementBook(CVCache(tmp_path / "c.json"), client, model="m")

    threads = [threading.Thread(target=book.of, args=(MATCH,)) for _ in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert len(client.calls) == 1
    again = RequirementBook(CVCache(tmp_path / "c.json"), FakeClient(), model="m")
    assert [one.name for one in again.of(MATCH).requirements] == ["Python"]


def test_the_requirements_are_read_without_the_cv(tmp_path):
    client = FakeClient(requirements_reply(PYTHON))
    RequirementBook(CVCache(tmp_path / "c.json"), client, model="m").of(MATCH)

    sent = "\n".join(message["content"] for message in client.calls[0]["messages"])
    assert "TypeScript en Python" not in sent
    assert "Van der Valk" in sent


def test_one_vacancy_end_to_end(tmp_path):
    client = FakeClient(
        requirements_reply(
            requirement("C#", quote="C#, Python en TypeScript"),
            requirement("Python", quote="C#, Python en TypeScript"),
            requirement("Azure", must=False, quote="Ervaring met Azure"),
        ),
        json.dumps(
            answer(
                (1, "", "missing"),
                (2, "TypeScript en Python", "met"),
                (3, "", "missing"),
            ).model_dump(mode="json")
        ),
    )
    book = RequirementBook(CVCache(tmp_path / "c.json"), client, model="m")

    judged = judge_by_requirements(CV, MATCH, client, book=book)

    # coverage 1 / 2.25 of the weight, work the same: 0.7 * 0.444 + 0.3
    assert judged.judgement.verdict is Verdict.POSSIBLE
    assert judged.judgement.fit == 61
    assert isinstance(judged.raw, RequirementJudgement)


def test_a_wish_the_vacancy_contradicts_keeps_it_from_strong():
    """Youssef's CV says Tilburg; 3.7 called a job in Geleen strong at 85."""
    cv = CV + "\nIk zoek werk in de regio Utrecht, fulltime."
    wish = ("fulltime", "fulltime", "Hbo werk- en denkniveau")
    everything = answer((1, "TypeScript en Python", "met"))

    free = combine(everything, Listed([PYTHON], []), cv, MATCH)
    bound = combine(
        answer((1, "TypeScript en Python", "met"), conflicts=[wish]),
        Listed([PYTHON], []),
        cv,
        MATCH,
    )

    assert free.judgement.verdict is Verdict.STRONG
    assert bound.judgement.verdict is Verdict.POSSIBLE
    assert bound.judgement.fit == 74
    assert bound.judgement.gaps[-1].requirement == "fulltime (a wish on your CV)"


def test_a_conflict_nobody_wrote_is_no_conflict():
    invented = ("regio Tilburg", "Ik woon in Tilburg", "Hbo werk- en denkniveau")
    judged = combine(
        answer((1, "TypeScript en Python", "met"), conflicts=[invented]),
        Listed([PYTHON], []),
        CV,
        MATCH,
    )

    assert judged.judgement.verdict is Verdict.STRONG
    assert judged.dropped[0].quote == "Ik woon in Tilburg"


PERMIT = requirement(
    "Werkvergunning", Kind.ELIGIBILITY, knockout=True, quote="Hbo werk- en denkniveau"
)


def test_a_cv_silent_about_a_knockout_is_not_ruled_out_by_it():
    """The first real run ruled out two jobs on a work permit no CV mentions."""
    judged = combine(
        answer((1, "TypeScript en Python", "met"), (2, "", "unknown")),
        Listed([PYTHON, PERMIT], []),
        CV,
        MATCH,
    )

    assert judged.judgement.verdict is Verdict.STRONG
    assert (
        judged.judgement.gaps[0].requirement == "Werkvergunning (your CV does not say)"
    )
    assert judged.judgement.gaps[0].knockout is False


def test_a_knockout_the_cv_shows_is_not_met_still_rules_out():
    judged = combine(
        answer((1, "TypeScript en Python", "met"), (2, "", "missing")),
        Listed([PYTHON, PERMIT], []),
        CV,
        MATCH,
    )

    assert judged.judgement.verdict is Verdict.WEAK
    assert judged.judgement.gaps[0].knockout is True


def test_unknown_about_a_skill_is_simply_missing():
    """Silence spares a knockout, never a skill: the CV not saying C# is the
    CV not having C#."""
    unknown = combine(
        answer((1, "TypeScript en Python", "met"), (2, "", "unknown")),
        Listed([PYTHON, CSHARP], []),
        CV,
        MATCH,
    )
    missing = combine(
        answer((1, "TypeScript en Python", "met"), (2, "", "missing")),
        Listed([PYTHON, CSHARP], []),
        CV,
        MATCH,
    )

    assert unknown.judgement.fit == missing.judgement.fit
    assert unknown.judgement.gaps[0].requirement == "C#"
