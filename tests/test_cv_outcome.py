"""The refusal, on the verdict counts that 3.5 and 3.6 actually measured.

Each case below is a real row from `scripts/eval_judge.py`, so if the rule ever
changes shape these tests say which measured CV it would start getting wrong.
"""

from joblens.cv.documents import QueryPart
from joblens.cv.judge import Judged, MatchJudgement, Verdict
from joblens.cv.match import CVMatch
from joblens.cv.outcome import Fit, assess
from joblens.sources.base import Vacancy

VACANCY = Vacancy(
    source="indeed",
    source_id="1",
    url="https://example.test",
    title="Verpleegkundige",
    company="Fivoor",
    city="Utrecht",
    text="Wij vragen ervaring.",
)


def judged(verdict: str, fit: int) -> Judged:
    judgement = MatchJudgement.model_validate(
        {
            "verdict": verdict,
            "fit": fit,
            "summary": "Een samenvatting.",
            "evidence": [],
            "gaps": [],
        }
    )
    match = CVMatch(VACANCY, 0.7, "document", QueryPart("the whole CV", "cv"))
    return Judged(match=match, judgement=judgement, dropped=[], quotes=0)


def test_all_weak_is_a_refusal():
    """ingrid_solheim in 3.6: 0 strong, 0 possible, 10 weak, best fit 5."""
    outcome = assess([judged("weak", 5)] + [judged("weak", 3)] * 9, corpus=279)
    assert outcome.fit is Fit.NOTHING
    assert outcome.refused
    assert outcome.best_fit == 5


def test_possible_without_strong_is_not_a_refusal():
    """lisa_de_vries in 3.6: 0 strong, 2 possible, 6 weak.

    The case that decides the rule has two bands. "No strong means refuse" would
    refuse a CV whose labels name four vacancies she would apply to.
    """
    outcome = assess(
        [judged("possible", 60), judged("possible", 55)] + [judged("weak", 20)] * 6,
        corpus=279,
    )
    assert outcome.fit is Fit.NO_CLEAR_FIT
    assert not outcome.refused


def test_one_strong_is_an_ordinary_answer():
    """sanne_vermeulen in 3.6: 4 strong on 4 'would apply'."""
    outcome = assess([judged("strong", 90)] + [judged("weak", 20)] * 9, corpus=279)
    assert outcome.fit is Fit.OK
    assert not outcome.advice()


def test_the_headline_claims_only_what_was_judged():
    """Ten vacancies were read, not 279, and the sentence has to say so."""
    outcome = assess([judged("weak", 5)] * 10, corpus=279, corpus_name="raw")
    headline = outcome.headline()
    assert "10 closest of 279" in headline
    assert "raw" in headline


def test_nothing_judged_is_not_a_statement_about_the_cv():
    """Every call failing looks like "all weak" to a counter. It is not."""
    outcome = assess([], corpus=279)
    headline = outcome.headline()
    assert "says nothing about your CV" in headline
    assert "closest" not in headline


def test_counts_cover_every_verdict_even_at_zero():
    outcome = assess([judged("weak", 5)], corpus=10)
    assert outcome.counts == {Verdict.STRONG: 0, Verdict.POSSIBLE: 0, Verdict.WEAK: 1}
