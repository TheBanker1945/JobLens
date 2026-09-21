"""Searching a set of vacancies by meaning.

The three parts already exist: which text to embed for a vacancy
(documents.py), what turns text into vectors (an EmbeddingClient, usually
wrapped in a CachedEmbedder) and how vectors are compared (similarity.rank).
This puts them together into the thing the rest of JobLens will ask questions
of -- a search script today, CV matching and an MCP tool later.

Documents are embedded on the first search and then kept, so a second query
costs one embedding call instead of one per vacancy. With a CachedEmbedder even
the first search is free on the second run.
"""

from dataclasses import dataclass
from typing import Protocol

from joblens.embeddings.documents import Style, build_document
from joblens.embeddings.similarity import Vector, rank
from joblens.extraction.schema import VacancyDetails
from joblens.sources.base import Vacancy

# An embedding server silently truncates what does not fit and still answers
# 200. Measured 2026-09-20 against Ollama 0.34.2: it serves qwen3-embedding:0.6b
# with a 4,096-token window (not the model's own 32,768), and vacancy text runs
# at about 5.4 characters per token, so anything past ~22,000 characters is
# dropped without a word. The longest vacancy we have is 11,188. This is the
# line where that stops being true, and `oversized` is how we hear about it.
LONG_DOCUMENT_CHARS = 20_000


class Embedder(Protocol):
    """What the index needs; EmbeddingClient and CachedEmbedder both fit."""

    def embed_documents(self, texts: list[str]) -> list[Vector]: ...

    def embed_query(self, query: str) -> Vector: ...


@dataclass(frozen=True)
class Match:
    vacancy: Vacancy
    score: float
    document: str  # what was actually embedded, so a hit can be explained


class VacancyIndex:
    def __init__(
        self, vacancies: list[Vacancy], documents: list[str], embedder: Embedder
    ):
        if len(vacancies) != len(documents):
            raise ValueError(
                f"{len(vacancies)} vacancies but {len(documents)} documents"
            )
        self.vacancies = vacancies
        self.documents = documents
        self.embedder = embedder
        self._vectors: list[Vector] | None = None

    @classmethod
    def build(
        cls,
        vacancies: list[Vacancy],
        details: dict[str, VacancyDetails],
        embedder: Embedder,
        style: Style = "structured",
    ) -> "VacancyIndex":
        """An index over the vacancies that can be written in this style.

        `structured` is the default since milestone 3.1, where it won on the real
        corpus with gemini-embedding-2 (88% hit@1 against 38% for the raw text).
        Real vacancies repeat their employer's boilerplate -- 29 Adyen adverts
        open with the same 600 words -- so embedding the raw text embeds the
        company as much as the job. The summary has none of that.

        Every style but `raw` is built from extracted fields, and a vacancy that
        has not been extracted yet has none. It is left out rather than quietly
        embedded as plain text: one index, one kind of document, or the scores
        stop being comparable.
        """
        chosen, documents = [], []
        for vacancy in vacancies:
            found = details.get(vacancy.key)
            if found is None and style != "raw":
                continue
            documents.append(build_document(vacancy.text, found, style))
            chosen.append(vacancy)
        return cls(chosen, documents, embedder)

    def vectors(self) -> list[Vector]:
        if self._vectors is None:
            self._vectors = self.embedder.embed_documents(self.documents)
        return self._vectors

    def search(self, query: str, top_k: int = 10) -> list[Match]:
        if not self.vacancies:
            return []
        hits = rank(self.embedder.embed_query(query), self.vectors(), top_k)
        return [
            Match(self.vacancies[hit.index], hit.score, self.documents[hit.index])
            for hit in hits
        ]

    def oversized(self, limit: int = LONG_DOCUMENT_CHARS) -> list[Vacancy]:
        """Vacancies whose document is long enough to risk being cut in half."""
        return [
            vacancy
            for vacancy, document in zip(self.vacancies, self.documents, strict=True)
            if len(document) > limit
        ]

    def __len__(self) -> int:
        return len(self.vacancies)
