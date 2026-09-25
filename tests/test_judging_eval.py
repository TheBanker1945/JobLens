"""The two order numbers the judge eval prints since 6.1."""

import pytest

from joblens.evals.judging import JudgeVariant, concordance, reordered_ndcg


def test_the_default_variant_keeps_the_name_old_answers_are_stored_under():
    """Every judgement bought before 6.2 is under "judgement-3.6"."""
    assert JudgeVariant().kind() == "judgement-3.6"


def test_every_setting_that_changes_the_answer_is_in_its_name():
    names = {
        JudgeVariant().kind(),
        JudgeVariant(temperature=1.0).kind(),
        JudgeVariant(thinking=True).kind(),
        JudgeVariant(sample=2).kind(),
        JudgeVariant(1.0, True, 2).kind(),
        JudgeVariant(method="requirements").kind(),
    }
    assert len(names) == 6


def test_concordance_is_one_when_every_pair_is_the_right_way_round():
    assert concordance([(90, 2), (50, 1), (10, 0)]) == 1.0


def test_concordance_is_zero_when_the_order_is_reversed():
    assert concordance([(10, 2), (50, 1), (90, 0)]) == 0.0


def test_a_tie_in_score_is_half_right():
    """The judge gave both 15: it did not choose, so it gets half."""
    assert concordance([(15, 2), (15, 0)]) == 0.5


def test_pairs_the_person_graded_alike_do_not_count():
    """Two "would not" vacancies have no right order to get wrong."""
    assert concordance([(90, 0), (10, 0), (50, 2)]) == 0.5


def test_no_differently_graded_pair_means_no_answer():
    """The control CV: every label is "would not"."""
    assert concordance([(5, 0), (15, 0)]) is None


def test_the_real_cv_shortlist_as_it_was_judged():
    """Fit and label per judged vacancy in the 2026-09-22 22:34 run: the one
    strong (84) is a "would not", the five "would apply" sit at 32 to 15, and
    the "maybe" at 20."""
    scored = [(84, 0), (32, 2), (30, 2), (28, 2), (25, 0), (24, 0), (22, 2)]
    scored += [(20, 1), (15, 0), (15, 2), (15, 0), (15, 0)]
    # 41 differently graded pairs: 25 the right way round, and three ties
    # between the "would apply" at 15 and the three "would not" at 15.
    assert concordance(scored) == pytest.approx(26.5 / 41)


def test_reordered_ndcg_scores_against_the_best_order_of_the_same_keys():
    grade = {"a": 2, "b": 0, "c": 1}.__getitem__

    assert reordered_ndcg(["a", "c", "b"], grade) == 1.0
    assert reordered_ndcg(["b", "c", "a"], grade) < 1.0


def test_reordered_ndcg_has_no_answer_without_a_gain():
    assert reordered_ndcg(["x", "y"], lambda key: 0) is None
