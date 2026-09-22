"""A call with a reason attached, and the lists the evals already read.

The milestone in one sentence: a label used to be a key in one of three lists,
and three lists cannot tell you whether the judge or the person was wrong. What
is tested here is that the reason is required, that it is kept exactly as typed,
and that adding one keeps the three lists -- which every eval scores through --
in step.
"""

import pytest

from joblens.evals.matching import Call, CVLabels, Decision


def labels() -> CVLabels:
    return CVLabels(cv="mahdi", corpus="raw", judged_by="Mahdi")


def decide(
    key: str, call: str, reason: str = "they ask 4 years, I would apply"
) -> Decision:
    return Decision(key=key, call=Call(call), reason=reason)


def test_a_call_updates_the_lists_the_eval_scores_through():
    one = labels()

    one.record(decide("indeed:1", "apply"))
    one.record(decide("indeed:2", "maybe"))
    one.record(decide("indeed:3", "no", "different profession"))

    assert one.relevant == ["indeed:1"]
    assert one.maybe == ["indeed:2"]
    assert one.judged == ["indeed:1", "indeed:2", "indeed:3"]
    assert one.grade("indeed:1") == 2 and one.grade("indeed:3") == 0


def test_a_call_without_a_reason_is_refused():
    """The whole point of 4.4: a y/n that carries no sentence is what we had."""
    with pytest.raises(ValueError, match="reason"):
        labels().record(decide("indeed:1", "apply", "   "))


def test_changing_your_mind_is_kept_as_history_and_the_latest_one_counts():
    one = labels()

    one.record(decide("indeed:1", "no", "too far from Utrecht"))
    one.record(decide("indeed:1", "apply", "they mention hybrid after all"))

    assert one.relevant == ["indeed:1"]
    assert one.maybe == []
    assert one.judged == ["indeed:1"]  # looked at once, whatever was said
    assert len(one.decisions) == 2  # both sentences survive
    assert one.decision_for("indeed:1").reason == "they mention hybrid after all"


def test_the_reason_is_stored_exactly_as_it_was_typed():
    """Nothing summarises it, groups it, or turns answers into a stated rule."""
    written = "hbo gevraagd maar ik heb de ervaring; ik solliciteer toch"
    one = labels().record(decide("indeed:1", "apply", written))

    assert one.decision_for("indeed:1").reason == written


def test_what_the_judge_said_is_kept_next_to_what_you_said():
    one = labels()

    one.record(
        Decision(
            key="indeed:1",
            call=Call.APPLY,
            reason="the years are negotiable",
            run="2026-09-22_1555_mohammed",
            verdict="weak",
            fit=30,
            rank=3,
        )
    )

    stored = one.decision_for("indeed:1")
    assert (stored.verdict, stored.fit, stored.rank) == ("weak", 30, 3)
    assert stored.run == "2026-09-22_1555_mohammed"


def test_labels_written_before_4_4_still_load_and_still_score():
    """The four invented CVs' files have lists and no decisions."""
    old = CVLabels.model_validate(
        {
            "cv": "lisa_de_vries",
            "corpus": "raw",
            "judged_by": "Claude",
            "relevant": ["indeed:1"],
            "maybe": ["indeed:2"],
            "judged": ["indeed:1", "indeed:2", "indeed:3"],
        }
    )

    assert old.decisions == []
    assert old.decision_for("indeed:1") is None
    assert old.grade("indeed:1") == 2

    old.record(decide("indeed:3", "apply", "I misjudged this one"))

    assert old.relevant == ["indeed:1", "indeed:3"]
