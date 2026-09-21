"""Cosine similarity and ranking, written out by hand.

    cos(a, b) = (a · b) / (|a| × |b|)

- a · b, the dot product: multiply the vectors position by position, add it all up.
- |a|, the length: the square root of a · a.

The result lies between -1 and 1 and only depends on the *direction* of the
vectors, not their length: 1 = same direction (same meaning), 0 = unrelated.
Scores are only meaningful relative to each other: 0.62 is not "a 62% match",
it is "closer than 0.55". Many embedding models (qwen3-embedding included) return
vectors of length 1 already; cosine then equals the plain dot product.
"""

import math
from dataclasses import dataclass

Vector = list[float]


def dot(a: Vector, b: Vector) -> float:
    if len(a) != len(b):
        raise ValueError(
            f"vectors have different sizes ({len(a)} vs {len(b)}): "
            "were they made by different embedding models?"
        )
    return sum(x * y for x, y in zip(a, b, strict=True))


def norm(a: Vector) -> float:
    return math.sqrt(dot(a, a))


def cosine_similarity(a: Vector, b: Vector) -> float:
    lengths = norm(a) * norm(b)
    if lengths == 0:
        raise ValueError("cosine similarity is undefined for a zero vector")
    return dot(a, b) / lengths


@dataclass(frozen=True)
class Hit:
    index: int  # position of the document in the list that was searched
    score: float
    query_index: int = 0  # which query it answered; only rank_pooled uses it


def rank(query: Vector, documents: list[Vector], top_k: int | None = None) -> list[Hit]:
    """Documents sorted by similarity to the query, most similar first."""
    hits = [Hit(i, cosine_similarity(query, doc)) for i, doc in enumerate(documents)]
    hits.sort(key=lambda hit: hit.score, reverse=True)
    return hits[:top_k] if top_k else hits


def rank_pooled(
    queries: list[Vector], documents: list[Vector], top_k: int | None = None
) -> list[Hit]:
    """Rank documents by their single best-matching query, not by the average.

    A CV is not one question. Asked as one vector it becomes the average of five
    jobs, an education and a pile of skills, and a vacancy that matches one job
    exactly matches that average weakly -- the same dilution that milestone 3.3
    measured on long vacancies, but worse, because a career really is several
    different things while an advert is one job.

    So each part of the CV asks separately and a vacancy keeps its best answer.
    `Hit.query_index` says which part won, which is what lets a result say "this
    matched your Coolblue job" rather than only "this matched you".
    """
    if not queries:
        raise ValueError("no queries to rank with")
    hits = []
    for index, document in enumerate(documents):
        scores = [cosine_similarity(query, document) for query in queries]
        best = max(range(len(scores)), key=scores.__getitem__)
        hits.append(Hit(index, scores[best], best))
    hits.sort(key=lambda hit: hit.score, reverse=True)
    return hits[:top_k] if top_k else hits
