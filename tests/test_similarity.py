import pytest

from joblens.embeddings.similarity import cosine_similarity, rank


@pytest.mark.parametrize(
    ("a", "b", "expected"),
    [
        ([1, 0], [1, 0], 1.0),  # same direction
        ([1, 0], [-1, 0], -1.0),  # opposite
        ([1, 0], [0, 1], 0.0),  # unrelated (perpendicular)
        ([1, 1], [1, 0], 0.7071),  # 45 degrees apart
    ],
)
def test_cosine_values(a, b, expected):
    assert cosine_similarity(a, b) == pytest.approx(expected, abs=1e-4)


def test_only_direction_counts_not_length():
    assert cosine_similarity([1, 2, 3], [10, 20, 30]) == pytest.approx(1.0)


def test_vectors_from_different_models_are_rejected():
    with pytest.raises(ValueError, match="different embedding models"):
        cosine_similarity([1, 0, 0], [1, 0])


def test_zero_vector_is_rejected():
    with pytest.raises(ValueError, match="zero vector"):
        cosine_similarity([0, 0], [1, 0])


def test_rank_sorts_most_similar_first():
    query = [1, 0]
    docs = [[0, 1], [1, 0.1], [-1, 0], [1, 1]]

    hits = rank(query, docs)

    assert [h.index for h in hits] == [1, 3, 0, 2]
    assert hits[0].score > hits[1].score > hits[2].score > hits[3].score


def test_rank_top_k():
    assert [h.index for h in rank([1, 0], [[0, 1], [1, 0], [1, 1]], top_k=2)] == [1, 2]
