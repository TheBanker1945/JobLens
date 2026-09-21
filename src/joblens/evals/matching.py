"""Measure CV matching: do the vacancies this person would apply to come out top?

The same shape as the retrieval eval next door, with one difference that matters.
There, a query is a line of text and there are 23 of them. Here, a "query" is a
whole person, and there are as many as there are labelled CVs -- three. Three is
far too few to average over, so this eval **never averages across CVs**. It prints
a row per CV per variant, and a variant wins by winning on each of them.

Metrics per CV:
- hit@1     is the first vacancy one this person would apply to?
- recall@10 what share of the vacancies they would apply to is in the top 10?
- MRR       1 / the position of the first one they would apply to.
- nDCG@10   the graded version: "would apply" counts more than "might", and a
            good answer counts for more the higher it is ranked. This is the one
            that uses the "maybe" judgements, and the one to read when hit@1 ties.
"""

import json
import math
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from pydantic import BaseModel

from joblens.cv.documents import CVStyle, QueryPart
from joblens.embeddings.client import Vector
from joblens.evals.retrieval import (
    Embedded,
    EmbedderPool,
    RetrievalConfig,
    embed,
)

# Graded relevance: what a label is worth when the ranking is scored.
GAIN = {"relevant": 2, "maybe": 1}


class MatchConfig(RetrievalConfig):
    """One [[run]] from evals/cv-matching.toml.

    Inherits the embedding settings from the retrieval eval -- provider, model,
    key, and `style`, which here is the style the *vacancies* are indexed in and
    stays `structured` because 3.1 settled it. `cv_style` is the new axis.
    """

    cv_style: CVStyle = "profile"


class CVLabels(BaseModel):
    """What one person judged about one CV against one corpus."""

    cv: str  # the CV's file stem
    corpus: str
    judged_by: str  # a name, so a number can be traced to whose opinion it rests on
    relevant: list[str] = []  # vacancy keys: would apply
    maybe: list[str] = []  # would consider, would not be annoyed to see
    judged: list[str] = []  # every key that was looked at, including the bad ones
    note: str = ""

    def grade(self, key: str) -> int:
        if key in self.relevant:
            return GAIN["relevant"]
        return GAIN["maybe"] if key in self.maybe else 0


class CVResult(BaseModel):
    cv: str
    ranked: list[str]  # every vacancy key, best first
    relevant: list[str]
    maybe: list[str] = []
    # The best score this CV got. Meaningless as a quality measure -- cosines are
    # not comparable between CVs -- but on a CV that fits nothing it is the
    # number a refusal has to be built from, so it is kept.
    top_score: float = 0.0

    @property
    def is_control(self) -> bool:
        """A CV with nothing to find. Every metric below is 0 for every variant,
        which says nothing about the variant, so the eval reports it apart."""
        return not self.relevant and not self.maybe

    @property
    def hit_at_1(self) -> float:
        return float(bool(self.ranked) and self.ranked[0] in self.relevant)

    def recall_at(self, k: int) -> float:
        if not self.relevant:
            return 0.0
        found = sum(key in self.relevant for key in self.ranked[:k])
        return found / len(self.relevant)

    @property
    def reciprocal_rank(self) -> float:
        for position, key in enumerate(self.ranked, 1):
            if key in self.relevant:
                return 1 / position
        return 0.0

    def ndcg_at(self, k: int = 10) -> float:
        """Graded gain, discounted by position, against the best possible order.

        gain = 2^grade - 1, so "would apply" (3) is worth three times "might" (1)
        rather than twice: the point of this app is the applications, not the
        maybes. 1.0 means nothing could have been ranked better.
        """
        gains = [self._gain(key) for key in self.ranked[:k]]
        ideal = sorted(
            [self._gain(key) for key in {*self.relevant, *self.maybe}], reverse=True
        )[:k]
        best = _discounted(ideal)
        return _discounted(gains) / best if best else 0.0

    def _gain(self, key: str) -> float:
        if key in self.relevant:
            return 2 ** GAIN["relevant"] - 1
        return 2 ** GAIN["maybe"] - 1 if key in self.maybe else 0.0


def _discounted(gains: list[float]) -> float:
    return sum(gain / math.log2(position + 1) for position, gain in enumerate(gains, 1))


class MatchRun(BaseModel):
    """One variant over every labelled CV."""

    variant: str
    cv_style: CVStyle
    model: str
    results: list[CVResult]
    seconds: float = 0.0
    api_calls: int = 0

    def for_cv(self, cv: str) -> CVResult | None:
        return next((r for r in self.results if r.cv == cv), None)

    def top(self, cv: str, k: int = 10) -> list[str]:
        result = self.for_cv(cv)
        return result.ranked[:k] if result else []


def overlap(first: list[str], second: list[str]) -> float:
    """How much two ranked lists share, as a fraction of the shorter one.

    Not a quality measure: a check that the app reads the CV at all. Two different
    people should get two different lists, and if they do not, nothing downstream
    of this is worth measuring. The brief asks for exactly this.
    """
    if not first or not second:
        return 0.0
    return len(set(first) & set(second)) / min(len(first), len(second))


def load_labels(directory: Path) -> list[CVLabels]:
    return [
        CVLabels.model_validate_json(path.read_text(encoding="utf-8"))
        for path in sorted(directory.glob("*.json"))
    ]


def save_labels(directory: Path, labels: CVLabels) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{labels.cv}.json"
    path.write_text(
        json.dumps(labels.model_dump(), indent=2, ensure_ascii=False), encoding="utf-8"
    )
    return path


@dataclass(frozen=True)
class Ranking:
    order: list[int]  # document indices, best first
    winners: list[int]  # which part of the CV won, in the same order
    scores: list[float]  # the winning cosine, in the same order


def rank_vacancies(
    config: MatchConfig,
    documents: list[str],
    parts: list[QueryPart],
    cache_dir: Path,
    pool: EmbedderPool | None = None,
) -> tuple[Ranking, Embedded]:
    """Rank the vacancy documents for one CV, in one variant's vector space.

    Shared by the eval and by scripts/label_cv_matches.py, for the same reason
    `embed` is shared on the retrieval side: the candidates a human judges have
    to come out of exactly the same embedding as the numbers reported later, or
    the labelling describes a different system.

    Returns the ranking and what the embedding cost.
    """
    embedded = embed(config, documents, [part.text for part in parts], cache_dir, pool)
    return pooled_ranking(embedded.queries, embedded.documents), embedded


def pooled_ranking(query_vectors: list[Vector], doc_vectors: list[Vector]) -> Ranking:
    """Each document scores as its best-matching query; ties keep input order.

    The numpy version of `similarity.rank_pooled`: normalise, one matrix multiply
    for every query-document cosine at once, then take the maximum down the query
    axis. 198 vacancies against a dozen parts of a CV is small, but the eval does
    it for every variant and every CV.
    """
    queries, docs = _unit(np.array(query_vectors)), _unit(np.array(doc_vectors))
    similarities = queries @ docs.T  # cosine: every row has length 1
    best = similarities.max(axis=0)
    winners = similarities.argmax(axis=0)
    order = np.argsort(-best, kind="stable")
    return Ranking(order.tolist(), winners[order].tolist(), best[order].tolist())


def _unit(matrix: np.ndarray) -> np.ndarray:
    lengths = np.linalg.norm(matrix, axis=1, keepdims=True)
    if np.any(lengths == 0):
        raise ValueError("cosine similarity is undefined for a zero vector")
    return matrix / lengths
