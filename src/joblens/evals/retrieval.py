"""Measure search quality: does the right vacancy come out on top?

Metrics (per query, then averaged):
- hit@1     was the top result relevant? (what the user sees first)
- recall@3  what share of the relevant vacancies is in the top 3?
  With 2 relevant vacancies, finding one of them scores 0.5.
- recall@10 the same over the top 10. On a corpus of 200 vacancies a broad query
  ("python developer") has more than 3 relevant answers, and recall@3 can then
  never reach 100% however good the ranking is -- recall@10 stays readable.
- MRR       1 / position of the first relevant result: 1st = 1.0, 2nd = 0.5,
            3rd = 0.33, none = 0. Rewards ranking higher, not just appearing.

Ranking uses numpy: normalise every vector to length 1, then one matrix multiply
gives all query-document cosines at once.
"""

import json
import os
import time
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from pydantic import BaseModel

from joblens.config import LLMSettings
from joblens.embeddings.client import EmbeddingClient, Vector
from joblens.embeddings.documents import Style
from joblens.embeddings.store import CachedEmbedder


class RetrievalConfig(BaseModel):
    """One [[run]] from evals/retrieval.toml."""

    name: str
    style: Style
    provider: str
    base_url: str
    model: str
    api_key_env: str | None = None  # env var name holding the key, never the key
    instruction: bool = True  # wrap queries in the model's task instruction
    chunk: bool = False  # split each document and score a vacancy by its best part

    def settings(self, env: Mapping[str, str] = os.environ) -> LLMSettings:
        api_key = "unused"  # local servers like Ollama ignore the key
        if self.api_key_env:
            api_key = env.get(self.api_key_env, "")
            if not api_key:
                raise ValueError(f"Run {self.name!r}: {self.api_key_env} is not set")
        return LLMSettings(
            provider=self.provider,
            base_url=self.base_url,
            api_key=api_key,
            model=self.model,
        )


class Query(BaseModel):
    split: str
    query: str
    relevant: list[str]  # Vacancy.key values that count as a correct answer
    judged: list[str] = []  # every key that was looked at, relevant or not
    note: str = ""


class QueryResult(BaseModel):
    query: str
    split: str
    ranked: list[str]  # every vacancy key, best first
    relevant: list[str]  # the keys that count as a correct answer

    @property
    def hit_at_1(self) -> float:
        return float(self.ranked[0] in self.relevant)

    def recall_at(self, k: int) -> float:
        found = sum(key in self.relevant for key in self.ranked[:k])
        return found / len(self.relevant)

    @property
    def reciprocal_rank(self) -> float:
        for position, key in enumerate(self.ranked, 1):
            if key in self.relevant:
                return 1 / position
        return 0.0


class RetrievalResult(BaseModel):
    variant: str
    style: Style
    model: str
    results: list[QueryResult]
    seconds: float = 0.0
    api_calls: int = 0

    def for_split(self, split: str) -> "RetrievalResult":
        keep = [r for r in self.results if r.split == split]
        return self.model_copy(update={"results": keep})

    @property
    def hit_at_1(self) -> float:
        return _mean(r.hit_at_1 for r in self.results)

    @property
    def recall_at_3(self) -> float:
        return _mean(r.recall_at(3) for r in self.results)

    @property
    def recall_at_10(self) -> float:
        return _mean(r.recall_at(10) for r in self.results)

    @property
    def mrr(self) -> float:
        return _mean(r.reciprocal_rank for r in self.results)


def _mean(values) -> float:
    values = list(values)
    return sum(values) / len(values) if values else 0.0


def rank_all(query_vectors: list[list[float]], doc_vectors: list[list[float]]):
    """Indices of the documents per query, most similar first (cosine)."""
    queries, docs = _unit(np.array(query_vectors)), _unit(np.array(doc_vectors))
    similarities = queries @ docs.T  # cosine, because every row has length 1
    return np.argsort(-similarities, axis=1, kind="stable")


def rank_all_pooled(
    query_vectors: list[list[float]],
    chunk_vectors: list[list[float]],
    owners: list[int],
    count: int,
):
    """Rank `count` vacancies by their single best-matching chunk.

    `owners[i]` says which vacancy chunk `i` belongs to. A vacancy scores as its
    strongest part rather than its average, which is the whole point of chunking:
    an advert that mentions Kubernetes once should not have that diluted by four
    paragraphs about the company.
    """
    queries, chunks = _unit(np.array(query_vectors)), _unit(np.array(chunk_vectors))
    similarities = queries @ chunks.T
    best = np.full((len(queries), count), -np.inf)
    np.maximum.at(best, (slice(None), np.array(owners)), similarities)
    return np.argsort(-best, axis=1, kind="stable")


def _unit(matrix: np.ndarray) -> np.ndarray:
    lengths = np.linalg.norm(matrix, axis=1, keepdims=True)
    if np.any(lengths == 0):
        raise ValueError("cosine similarity is undefined for a zero vector")
    return matrix / lengths


@dataclass(frozen=True)
class Embedded:
    """One variant's vectors, plus what they cost to get."""

    documents: list[Vector]
    queries: list[Vector]
    api_calls: int
    seconds: float


class EmbedderPool:
    """One embedder per model, kept open for the length of a run.

    Without this, every variant builds its own `CachedEmbedder`, and building one
    means reading the whole cache file: for gemini-embedding-2 that file is 171 MB
    of JSON, and the CV eval asks for eight variants across three CVs. Reading it
    twenty-four times took longer than every embedding call put together.

    Keyed by provider, base URL and model, because that is what decides which
    vector space you are in -- two variants that differ only in which document
    style they embed share everything here.
    """

    def __init__(self, cache_dir: Path):
        self.cache_dir = cache_dir
        self._embedders: dict[tuple[str, str, str], CachedEmbedder] = {}
        self._clients: list[EmbeddingClient] = []

    def get(self, config: RetrievalConfig) -> CachedEmbedder:
        settings = config.settings()
        key = (settings.provider, settings.base_url, settings.model)
        if key not in self._embedders:
            client = EmbeddingClient(settings)
            self._clients.append(client)
            path = (
                self.cache_dir / f"embeddings-{settings.model.replace(':', '-')}.json"
            )
            self._embedders[key] = CachedEmbedder(client, path)
        return self._embedders[key]

    def close(self) -> None:
        for client in self._clients:
            client.close()

    def __enter__(self) -> "EmbedderPool":
        return self

    def __exit__(self, *exc) -> None:
        self.close()


def embed(
    config: RetrievalConfig,
    documents: list[str],
    queries: list[str],
    cache_dir: Path,
    pool: EmbedderPool | None = None,
) -> Embedded:
    """Put documents and queries in one variant's vector space.

    Shared by the eval and by scripts/label_queries.py: the candidates a human
    judges must come out of exactly the same embedding as the numbers reported
    afterwards, or the labelling is describing a different system.

    Pass a `pool` when several variants run in one go; without one, this opens a
    client and reads the cache file for itself and closes both afterwards.
    """
    if pool is not None:
        return _embed_with(pool.get(config), config, documents, queries)
    cache_path = cache_dir / f"embeddings-{config.model.replace(':', '-')}.json"
    with EmbeddingClient(config.settings()) as client:
        return _embed_with(
            CachedEmbedder(client, cache_path), config, documents, queries
        )


def _embed_with(
    embedder: CachedEmbedder,
    config: RetrievalConfig,
    documents: list[str],
    queries: list[str],
) -> Embedded:
    """A pooled embedder is reused, so what it cost is counted per call."""
    before = embedder.misses
    start = time.perf_counter()
    doc_vectors = embedder.embed_documents(documents)
    query_vectors = [
        embedder.embed_query(query)
        if config.instruction
        else embedder.embed_documents([query])[0]
        for query in queries
    ]
    return Embedded(
        doc_vectors,
        query_vectors,
        embedder.misses - before,
        time.perf_counter() - start,
    )


def load_configs(path: Path) -> list[RetrievalConfig]:
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    return [RetrievalConfig.model_validate(run) for run in data["run"]]


def load_queries(path: Path) -> list[Query]:
    return [Query.model_validate(q) for q in json.loads(path.read_text("utf-8"))]


def merge_draft(
    draft: list[dict], existing: list[Query]
) -> tuple[list[Query], list[str]]:
    """Draft queries, carrying over answers already given for the same text.

    The draft decides which queries exist, so one edited or removed there loses
    its labels -- an edited query is a different question and its old answers
    cannot be trusted. Returns the merged queries and the texts that were
    dropped, so a labelling session can say what it is about to forget.
    """
    answered = {query.query: query for query in existing}
    merged = [
        Query(
            split=item["split"],
            query=item["query"],
            note=item.get("note", ""),
            relevant=list(answered[item["query"]].relevant)
            if item["query"] in answered
            else [],
            judged=list(answered[item["query"]].judged)
            if item["query"] in answered
            else [],
        )
        for item in draft
    ]
    dropped = sorted(set(answered) - {query.query for query in merged})
    return merged, dropped
