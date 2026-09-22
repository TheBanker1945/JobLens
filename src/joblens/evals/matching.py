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

import math
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from pathlib import Path

import numpy as np
from pydantic import BaseModel, Field

from joblens.cv.documents import CVStyle, QueryPart
from joblens.embeddings.client import Vector
from joblens.embeddings.similarity import fuse_orders
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
    # More ways of asking, fused with the first (see `fuse`). Empty is one list.
    fuse_with: list[CVStyle] = []

    @property
    def styles(self) -> list[CVStyle]:
        return [self.cv_style, *self.fuse_with]


class Call(StrEnum):
    """The three answers. The same three the CLI asked for, with a reason now."""

    APPLY = "apply"
    MAYBE = "maybe"
    NO = "no"


class Decision(BaseModel):
    """One call by the person the CV belongs to, and **why**.

    The why is the milestone. Until 4.4 a label was a key in one of three lists,
    which is enough to score a ranking and not enough to improve one: Mahdi
    disagreed with the judge five times out of ten and nobody -- including him a
    week later -- could say whether the system or he was wrong, because the
    disagreement carried no sentence.

    What is stored next to the reason is what the judge had said at that moment.
    A reason read a month later against a verdict that has since changed is a
    different sentence, and the eval compares the two directly.

    The reason is written down exactly as it was typed. Nothing summarises it,
    groups it, or turns a pattern of answers into a rule -- that is the one thing
    the brief said never to do, and a "rule" nobody stated is a rule nobody can
    correct.
    """

    key: str
    call: Call
    reason: str
    at: datetime = Field(default_factory=datetime.now)
    run: str = ""  # the run that was on screen, so the numbers can be found again
    verdict: str = ""  # what the judge said about it then
    fit: int | None = None
    rank: int | None = None  # where retrieval had put it


class CVLabels(BaseModel):
    """What one person judged about one CV against one corpus."""

    cv: str  # the CV's file stem
    corpus: str
    judged_by: str  # a name, so a number can be traced to whose opinion it rests on
    relevant: list[str] = []  # vacancy keys: would apply
    maybe: list[str] = []  # would consider, would not be annoyed to see
    judged: list[str] = []  # every key that was looked at, including the bad ones
    # Append-only, newest last: a person changing their mind about a vacancy is
    # data, not a correction to be overwritten. `decision_for` reads the latest.
    # The three lists above stay in step so every eval keeps working unchanged.
    decisions: list[Decision] = []
    note: str = ""

    def grade(self, key: str) -> int:
        if key in self.relevant:
            return GAIN["relevant"]
        return GAIN["maybe"] if key in self.maybe else 0

    def record(self, decision: Decision) -> "CVLabels":
        """Add a call, and keep the three lists it is scored through in step."""
        if not decision.reason.strip():
            raise ValueError("a decision needs a reason: that is the whole point")
        self.decisions.append(decision)
        self.relevant = [key for key in self.relevant if key != decision.key]
        self.maybe = [key for key in self.maybe if key != decision.key]
        if decision.call is Call.APPLY:
            self.relevant.append(decision.key)
        elif decision.call is Call.MAYBE:
            self.maybe.append(decision.key)
        if decision.key not in self.judged:
            self.judged.append(decision.key)
        return self

    def decision_for(self, key: str) -> Decision | None:
        """The latest thing this person said about this vacancy."""
        return next((one for one in reversed(self.decisions) if one.key == key), None)

    def reasons(self) -> dict[str, Decision]:
        return {one.key: one for one in self.decisions}  # later entries win


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

    def labelled_share(self, judged: set[str], k: int = 10) -> float:
        """How much of the top k was ever looked at by whoever labelled this CV.

        The number that says whether these labels still cover the corpus. A
        vacancy nobody judged scores gain 0 exactly like one judged "would not
        apply", so an unlabelled top-10 does not read as a warning -- it reads as
        a worse variant. The labels were pooled over 198 vacancies and the corpus
        is 279, so this has to be printed next to every score above it.
        """
        head = self.ranked[:k]
        if not head:
            return 1.0
        return sum(key in judged for key in head) / len(head)

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
    fuse_with: list[CVStyle] = []
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


def fuse(rankings: list[Ranking]) -> Ranking:
    """Reciprocal rank fusion of several rankings (see `similarity.fuse_orders`).

    Why it can beat either list alone: 3.5 found no CV style that wins on every
    CV -- each loses on a different person -- and a vacancy that two different
    questions both put near the top is less likely to be one list's quirk.

    `winners` says which list placed the vacancy best; `scores` are the fused
    points, not a cosine.
    """
    fused = fuse_orders([ranking.order for ranking in rankings])
    return Ranking(
        order=[one.item for one in fused],
        winners=[one.best_list for one in fused],
        scores=[one.points for one in fused],
    )


@dataclass(frozen=True)
class CVRanking:
    """A variant's ranking for one CV, and the lists it was fused from."""

    ranking: Ranking
    lists: list[Ranking]  # one per style; the ranking itself when not fused
    seconds: float
    api_calls: int

    @property
    def top_cosine(self) -> float:
        """The best cosine any of the lists gave: fused points are not a cosine."""
        return max(one.scores[0] for one in self.lists)


def rank_for_cv(
    config: MatchConfig,
    documents: list[str],
    parts_for: Callable[[CVStyle], list[QueryPart]],
    cache_dir: Path,
    pool: EmbedderPool | None = None,
) -> CVRanking:
    """One variant's ranking of the vacancies for one CV, fused if it says so.

    Shared by the eval and the labelling script for the reason `rank_vacancies`
    is: the candidates a human judges have to come out of the same ranking the
    numbers are reported for. `parts_for(style)` turns the CV into that style's
    query parts; the caller owns the CV and the model that may write a wishlist.
    """
    lists, seconds, calls = [], 0.0, 0
    for style in config.styles:
        ranking, embedded = rank_vacancies(
            config, documents, parts_for(style), cache_dir, pool
        )
        lists.append(ranking)
        seconds += embedded.seconds
        calls += embedded.api_calls
    fused = lists[0] if len(lists) == 1 else fuse(lists)
    return CVRanking(fused, lists, seconds, calls)
