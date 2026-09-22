"""The numbers the CV eval reports, and the pooled ranking underneath them."""

import pytest

from joblens.evals.matching import (
    CVLabels,
    CVResult,
    Ranking,
    fuse,
    overlap,
    pooled_ranking,
)

APPLY = ["a", "b"]
MAYBE = ["c"]


def result(*ranked: str) -> CVResult:
    return CVResult(cv="test", ranked=list(ranked), relevant=APPLY, maybe=MAYBE)


def test_hit_at_1_is_about_the_first_line_only():
    assert result("a", "z").hit_at_1 == 1.0
    assert result("c", "a").hit_at_1 == 0.0  # a "maybe" on top is not a hit


def test_recall_counts_what_was_found_of_what_there_was():
    assert result("a", "z", "b").recall_at(3) == 1.0
    assert result("a", "z", "z").recall_at(3) == 0.5


def test_reciprocal_rank_rewards_being_higher_not_just_present():
    assert result("z", "a").reciprocal_rank == 0.5
    assert result("z", "z", "z").reciprocal_rank == 0.0


def test_ndcg_is_1_only_when_nothing_could_be_ranked_better():
    assert result("a", "b", "c").ndcg_at(10) == 1.0
    assert result("c", "a", "b").ndcg_at(10) < 1.0


def test_ndcg_prefers_an_application_over_two_maybes():
    """gain 3 against 1: this app is for deciding where to apply, and a list of
    near misses at the top is not that."""
    apply_first = CVResult(cv="t", ranked=["a", "c"], relevant=["a"], maybe=["c"])
    maybe_first = CVResult(cv="t", ranked=["c", "a"], relevant=["a"], maybe=["c"])

    assert apply_first.ndcg_at(10) > maybe_first.ndcg_at(10)


def test_a_cv_with_nothing_to_find_is_a_control_and_scores_nothing():
    control = CVResult(cv="ingrid", ranked=["x", "y"], relevant=[], maybe=[])

    assert control.is_control
    assert control.ndcg_at(10) == 0.0
    assert control.recall_at(10) == 0.0


def test_grades_come_from_the_labels():
    labels = CVLabels(
        cv="t", corpus="raw", judged_by="test", relevant=["a"], maybe=["b"]
    )

    assert (labels.grade("a"), labels.grade("b"), labels.grade("z")) == (2, 1, 0)


@pytest.mark.parametrize(
    ("first", "second", "expected"),
    [
        (["a", "b"], ["a", "b"], 1.0),
        (["a", "b"], ["c", "d"], 0.0),
        (["a", "b"], ["b", "c", "d"], 0.5),  # of the shorter list
        ([], ["a"], 0.0),
    ],
)
def test_overlap_measures_whether_two_people_get_two_lists(first, second, expected):
    assert overlap(first, second) == expected


def test_a_document_is_ranked_by_its_best_part_and_remembers_which():
    care, data = [1.0, 0.0], [0.0, 1.0]

    ranking = pooled_ranking([care, data], [[0.9, 0.1], [0.1, 0.9], [0.5, 0.5]])

    assert ranking.order == [0, 1, 2]
    assert ranking.winners == [0, 1, 0]  # doc 0 by the care part, doc 1 by data
    assert ranking.scores[0] > ranking.scores[2]


def test_pooling_takes_the_best_part_not_the_average():
    """A CV whose second half is about something else must not drag down a
    vacancy that its first half answers perfectly."""
    parts = [[1.0, 0.0], [0.0, 1.0]]

    ranking = pooled_ranking(parts, [[1.0, 0.0]])

    assert ranking.scores[0] == pytest.approx(1.0)


def _ranking(*order: int) -> Ranking:
    return Ranking(
        order=list(order), winners=[0] * len(order), scores=[0.5] * len(order)
    )


def test_fusion_prefers_what_both_lists_rank_well():
    """Second in both beats first in one and last in the other."""
    fused = fuse([_ranking(0, 1, 2, 3), _ranking(3, 1, 2, 0)])

    assert fused.order[0] == 1
    assert set(fused.order) == {0, 1, 2, 3}


def test_fusion_never_compares_the_scores_of_two_lists():
    """A list with much higher cosines gets no more say than one with low ones."""
    loud = Ranking(order=[0, 1], winners=[0, 0], scores=[0.99, 0.98])
    quiet = Ranking(order=[1, 0], winners=[0, 0], scores=[0.21, 0.20])

    fused = fuse([loud, quiet])

    assert fused.scores[0] == fused.scores[1]  # a dead heat, as it should be
    assert fused.order == [0, 1]  # and a tie keeps the first list's order


def test_fusion_says_which_list_placed_a_document_best():
    fused = fuse([_ranking(0, 1, 2), _ranking(2, 0, 1)])
    placed = dict(zip(fused.order, fused.winners, strict=True))

    assert placed[2] == 1  # first in the second list, last in the first
    assert placed[0] == 0
