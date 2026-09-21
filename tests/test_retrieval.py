import pytest

from joblens.embeddings.similarity import rank as rank_by_hand
from joblens.evals.retrieval import Query, QueryResult, merge_draft, rank_all

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


def query(text, *, relevant=(), judged=(), note=""):
    return Query(
        split="dev",
        query=text,
        relevant=list(relevant),
        judged=list(judged),
        note=note,
    )


def test_merge_draft_carries_over_answers_already_given():
    draft = [{"split": "dev", "query": "liftmonteur", "note": "narrow"}]
    existing = [query("liftmonteur", relevant=["a"], judged=["a", "b"])]

    merged, dropped = merge_draft(draft, existing)

    assert (merged[0].relevant, merged[0].judged) == (["a"], ["a", "b"])
    assert merged[0].note == "narrow"  # the draft wins on everything but answers
    assert dropped == []


def test_merge_draft_starts_a_new_query_empty():
    merged, dropped = merge_draft(
        [{"split": "holdout", "query": "docent wiskunde"}], []
    )

    assert (merged[0].relevant, merged[0].judged, merged[0].split) == (
        [],
        [],
        "holdout",
    )
    assert dropped == []


def test_merge_draft_reports_a_query_whose_answers_it_drops():
    existing = [query("oude vraag", relevant=["a"]), query("liftmonteur")]

    merged, dropped = merge_draft([{"split": "dev", "query": "liftmonteur"}], existing)

    assert [m.query for m in merged] == ["liftmonteur"]
    assert dropped == ["oude vraag"]


def test_merge_draft_does_not_share_lists_with_what_it_merged():
    existing = [query("liftmonteur", relevant=["a"], judged=["a"])]

    merged, _ = merge_draft([{"split": "dev", "query": "liftmonteur"}], existing)
    merged[0].relevant.append("b")

    assert existing[0].relevant == ["a"]  # labelling must not mutate what it read
