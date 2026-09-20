import pytest

from joblens.embeddings.similarity import rank as rank_by_hand
from joblens.evals.retrieval import QueryResult, rank_all

RANKED = ["a", "b", "c", "d"]


def result(relevant):
    return QueryResult(query="q", split="dev", ranked=RANKED, relevant=relevant)


@pytest.mark.parametrize(
    ("relevant", "hit1", "recall3", "rr"),
    [
        (["a"], 1.0, 1.0, 1.0),  # perfect
        (["b"], 0.0, 1.0, 0.5),  # second place
        (["d"], 0.0, 0.0, 0.25),  # last: not in top 3
        (["a", "c"], 1.0, 1.0, 1.0),  # both in top 3
        (["a", "d"], 1.0, 0.5, 1.0),  # one of two found in top 3
    ],
)
def test_metrics(relevant, hit1, recall3, rr):
    r = result(relevant)

    assert (r.hit_at_1, r.recall_at(3), r.reciprocal_rank) == (hit1, recall3, rr)


def test_no_relevant_result_scores_zero():
    r = QueryResult(query="q", split="dev", ranked=RANKED, relevant=["zzz"])

    assert r.reciprocal_rank == 0.0
    assert r.recall_at(3) == 0.0


def test_numpy_ranking_matches_the_hand_written_cosine():
    docs = [[1.0, 0.0], [0.0, 1.0], [1.0, 1.0], [-1.0, 0.5]]
    queries = [[1.0, 0.1], [0.2, 1.0]]

    fast = rank_all(queries, docs)

    for row, query in zip(fast, queries, strict=True):
        by_hand = [hit.index for hit in rank_by_hand(query, docs)]
        assert list(row) == by_hand


def test_zero_vector_is_rejected():
    with pytest.raises(ValueError, match="zero vector"):
        rank_all([[0.0, 0.0]], [[1.0, 0.0]])
