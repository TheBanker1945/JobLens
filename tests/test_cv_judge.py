"""The judge, and the check that decides what a reader is allowed to see.

The quote check is the load-bearing part of this milestone: the prompt asks the
model not to invent experience, and `verify` is what happens when it does anyway.
"""

import json

import pytest
from conftest import FakeClient
from pydantic import ValidationError

from joblens.cv.documents import QueryPart
from joblens.cv.judge import (
    MatchJudgement,
    Verdict,
    judge_match,
    judge_matches,
    shown_vacancy,
    verify,
)
from joblens.cv.match import CVMatch
from joblens.cv.verify import quoted, searchable
from joblens.sources.base import Vacancy

CV = """**Verpleegkundige, gesloten opnameafdeling — Altrecht, Den Dolder**
mei 2022 - heden
Medicatie delen en bewaken, somatische screening.

- BIG-registratie verpleegkundige (geldig t/m 2029)
"""
VACANCY_TEXT = (
    "Wij vragen: je hebt een afgeronde hbo-opleiding\nen minimaal 3 jaar ervaring."
)

VACANCY = Vacancy(
    source="indeed",
    source_id="1",
    url="https://example.test",
    title="Verpleegkundige",
    company="Fivoor",
    city="Utrecht",
    text=VACANCY_TEXT,
)
MATCH = CVMatch(VACANCY, 0.8, "document", QueryPart("the whole CV", CV))


def judgement(**stated) -> MatchJudgement:
    blank = {
        "verdict": "strong",
        "fit": 80,
        "summary": "Je hebt de gevraagde registratie.",
        "evidence": [],
        "gaps": [],
    }
    return MatchJudgement.model_validate(blank | stated)


def evidence(quote: str) -> dict:
    return {"requirement": "BIG-registratie", "cv_quote": quote}


def gap(quote: str) -> dict:
    return {
        "requirement": "hbo",
        "vacancy_quote": quote,
        "required": True,
        "knockout": False,
    }


def test_a_quote_that_crosses_a_line_break_is_still_a_quote():
    """The CV has "Altrecht, Den Dolder" and "mei 2022" on two lines; a model
    quoting both writes one line, and that is the same words."""
    checked = verify(
        judgement(evidence=[evidence("Altrecht, Den Dolder mei 2022 - heden")]),
        CV,
        VACANCY_TEXT,
    )

    assert len(checked.judgement.evidence) == 1
    assert checked.faithfulness == 1.0


def test_markdown_and_typographic_characters_are_formatting_not_words():
    quoted = "Verpleegkundige, gesloten opnameafdeling - Altrecht, Den Dolder"

    checked = verify(judgement(evidence=[evidence(quoted)]), CV, VACANCY_TEXT)

    assert len(checked.judgement.evidence) == 1


def test_an_invented_quote_loses_its_claim():
    checked = verify(
        judgement(evidence=[evidence("tien jaar ervaring met Kubernetes")]),
        CV,
        VACANCY_TEXT,
    )

    assert checked.judgement.evidence == []
    assert checked.faithfulness == 0.0
    assert checked.dropped[0].kind == "cv"
    assert checked.dropped[0].claim == "BIG-registratie"


def test_a_real_quote_from_the_wrong_text_is_still_dropped():
    """The vacancy says "minimaal 3 jaar ervaring"; that is not evidence about
    the person, however literally true the words are."""
    checked = verify(
        judgement(evidence=[evidence("minimaal 3 jaar ervaring")]), CV, VACANCY_TEXT
    )

    assert checked.judgement.evidence == []


def test_a_gap_is_checked_against_the_vacancy():
    kept = verify(
        judgement(gaps=[gap("een afgeronde hbo-opleiding")]), CV, VACANCY_TEXT
    )
    invented = verify(
        judgement(gaps=[gap("je spreekt vloeiend Duits")]), CV, VACANCY_TEXT
    )

    assert len(kept.judgement.gaps) == 1
    assert invented.judgement.gaps == []
    assert invented.dropped[0].kind == "vacancy"


def test_an_empty_quote_proves_nothing():
    checked = verify(judgement(evidence=[evidence("   ")]), CV, VACANCY_TEXT)

    assert checked.judgement.evidence == []


def test_faithfulness_counts_every_quote_of_both_kinds():
    checked = verify(
        judgement(
            evidence=[
                evidence("BIG-registratie verpleegkundige"),
                evidence("nonsense"),
            ],
            gaps=[gap("een afgeronde hbo-opleiding")],
        ),
        CV,
        VACANCY_TEXT,
    )

    assert checked.quotes == 3
    assert checked.faithfulness == pytest.approx(2 / 3)


@pytest.mark.parametrize(
    ("verdict", "fit"), [("strong", 40), ("weak", 80), ("possible", 90)]
)
def test_the_number_has_to_agree_with_the_band(verdict, fit):
    """Otherwise "weak, 90" reaches the report and means nothing at all. The
    repair loop turns this into a second attempt."""
    with pytest.raises(ValueError, match="outside the"):
        judgement(verdict=verdict, fit=fit)


def test_the_cv_goes_first_so_a_provider_can_cache_it():
    client = FakeClient(json.dumps(judgement().model_dump(mode="json")))

    judge_match(CV, MATCH, client)

    sent = client.calls[0]["messages"][1]["content"]
    assert sent.index("## The CV") < sent.index("## The vacancy")
    assert VACANCY.title in sent


def test_the_model_answer_is_kept_next_to_the_checked_one():
    """What the model said is what is worth storing: re-checking it later is
    free, asking again is not."""
    answer = judgement(evidence=[evidence("invented")])
    client = FakeClient(json.dumps(answer.model_dump(mode="json")))

    result = judge_match(CV, MATCH, client)

    assert result.judgement.evidence == []  # what a reader sees
    assert len(result.raw.evidence) == 1  # what the model actually claimed


def test_the_list_is_ordered_by_the_verdict_not_by_the_retrieval_score():
    """Retrieval chose who got a call; it does not choose the order."""
    close_but_weak = CVMatch(VACANCY, 0.99, "d", QueryPart("cv", CV))
    distant_but_strong = CVMatch(VACANCY, 0.10, "d", QueryPart("cv", CV))
    client = FakeClient(
        json.dumps(judgement(verdict="weak", fit=10).model_dump(mode="json")),
        json.dumps(judgement(verdict="strong", fit=90).model_dump(mode="json")),
    )

    judged, failures = judge_matches(
        CV, [close_but_weak, distant_but_strong], client, workers=1
    )

    assert not failures
    assert [one.judgement.verdict for one in judged] == [Verdict.STRONG, Verdict.WEAK]


def test_one_vacancy_failing_does_not_lose_the_others():
    client = FakeClient(
        "not json at all",
        "still not json",
        json.dumps(judgement().model_dump(mode="json")),
    )

    judged, failures = judge_matches(CV, [MATCH, MATCH], client, workers=1)

    assert len(judged) == 1
    assert len(failures) == 1
    assert "indeed:1" in failures[0]


def test_each_judgement_is_handed_over_as_it_arrives():
    """An eval stores every answer before the next one lands, so a run that
    dies halfway keeps what it already paid for."""
    client = FakeClient(
        json.dumps(judgement().model_dump(mode="json")),
        "not json at all",
        "still not json",
    )
    seen = []

    judged, failures = judge_matches(
        CV, [MATCH, MATCH], client, workers=1, on_judged=seen.append
    )

    assert seen == judged
    assert len(seen) == 1 and len(failures) == 1


@pytest.mark.parametrize(
    ("quote", "source"),
    [
        ("Java", "Frontend in JavaScript en TypeScript"),
        ("Scala", "Scalable systems gebouwd"),
        ("Excel", "Excellent communicator"),
        ("Go", "Werkte bij Google"),
        ("C#", "Vijf jaar C++"),
        ("C", "Vijf jaar C++"),
    ],
)
def test_a_quote_must_be_whole_words_in_the_source(quote, source):
    """A substring test passed every one of these (2026-09-22 audit)."""
    assert not quoted(quote, searchable(source))


@pytest.mark.parametrize(
    ("quote", "source"),
    [
        ("C#", "Backend in C# en .NET"),
        ("C++", "Vijf jaar C++, daarna Rust."),
        ("Werkervaring", "## Werkervaring\nData-analist"),
        ("English (fluent)", "Talen: English (fluent), Dutch"),
    ],
)
def test_whole_word_quotes_still_pass(quote, source):
    assert quoted(quote, searchable(source))


def knockout_gap(required: bool = True) -> dict:
    return gap("een afgeronde hbo-opleiding") | {
        "requirement": "BIG-registratie",
        "required": required,
        "knockout": True,
    }


def test_a_knockout_makes_the_verdict_weak():
    """The one gap that decides the verdict on its own: the schema holds the
    model to it, the way it holds the number to the band."""
    with pytest.raises(ValidationError, match="knockout"):
        judgement(verdict="possible", fit=50, gaps=[knockout_gap()])

    weak = judgement(verdict="weak", fit=10, gaps=[knockout_gap()])
    assert weak.gaps[0].knockout


def test_a_nice_to_have_cannot_rule_anyone_out():
    with pytest.raises(ValidationError, match="nice-to-have"):
        judgement(verdict="weak", fit=10, gaps=[knockout_gap(required=False)])


def test_years_short_is_not_a_knockout_and_leaves_the_verdict_open():
    """A stretch gap can sit under any verdict: it moves the fit, not the band."""
    stretch = gap("minimaal 3 jaar ervaring")
    assert judgement(verdict="strong", fit=78, gaps=[stretch]).verdict is Verdict.STRONG


def test_the_reasons_are_written_before_the_verdict():
    """A model writes fields in schema order, so the order is the reasoning."""
    order = list(MatchJudgement.model_json_schema()["properties"])
    assert order == ["evidence", "gaps", "summary", "verdict", "fit"]


def test_the_heading_the_model_was_shown_can_be_quoted():
    """The two quotes 6.1's baseline dropped were the vacancy's heading line,
    which the model had been given."""
    shown = shown_vacancy(MATCH)

    kept = verify(
        judgement(gaps=[gap("Verpleegkundige (Fivoor · Utrecht)")]), CV, shown
    )
    ours = verify(judgement(gaps=[gap("The vacancy: Verpleegkundige")]), CV, shown)

    assert len(kept.judgement.gaps) == 1
    assert ours.judgement.gaps == []
