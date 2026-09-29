"""The vacancies a hosted server ranks, and their vectors, in Postgres (7.8.1).

A server on a laptop reads the corpus from data/raw and the vectors from
data/cache. A hosted container has no lasting disk, so `publish` copies the
open, extracted vacancies and their vectors into the database (migration
0006), and a server started with `--corpus db` reads them back with `load`
and `published_vectors`.

**What is published is exactly what a server would have loaded**: the corpus
`load_corpus("raw", open_only=True).extracted()` gives, in its order, with its
funnel, and one vector per vacancy, keyed as the local cache keys it. A match
ranked from here is therefore the match ranked from disk (tests/test_published.py
holds that).

**Nothing about a person goes here.** The published tables hold public
adverts. A CV's own vectors are embedded when a match asks and kept in the
server's local, temporary cache (`LayeredVectors`), never in the shared
table: that table has no user_id, so deleting an account could not reach
them.
"""

from dataclasses import dataclass
from datetime import datetime

from psycopg.types.json import Jsonb

from joblens.corpus import Corpus, Funnel
from joblens.embeddings.client import Vector
from joblens.embeddings.index import VacancyIndex
from joblens.embeddings.store import (
    CachedEmbedder,
    VectorStore,
    pack,
    unpack,
    vector_key,
)
from joblens.extraction.schema import VacancyDetails
from joblens.sources.base import Vacancy
from joblens.storage.postgres import Database


@dataclass(frozen=True)
class Published:
    at: datetime
    vacancies: int
    vectors: int
    model: str


def publish(
    database: Database, corpus: Corpus, vectors: dict[str, Vector], model: str
) -> Published:
    """Replace what is published with `corpus` and `vectors` (by cache key).

    One transaction: a server starting while this runs reads the old set or
    the new one, never half of each. Every vacancy must be extracted --
    that is what a server ranks -- and every vector must be for `model`.
    """
    missing = [v.key for v in corpus.vacancies if v.key not in corpus.details]
    if missing:
        raise ValueError(
            f"{len(missing)} vacancies are not extracted, e.g. {missing[0]}"
        )
    with database.connect() as conn, conn.transaction():
        conn.execute("DELETE FROM vacancies")
        with conn.cursor().copy(
            "COPY vacancies (key, position, record, details) FROM STDIN"
        ) as copy:
            copy.set_types(["text", "int4", "jsonb", "jsonb"])
            for position, vacancy in enumerate(corpus.vacancies):
                # The jsonb type encodes a dict itself; a JSON string here
                # would be stored as a string holding JSON.
                copy.write_row(
                    (
                        vacancy.key,
                        position,
                        Jsonb(vacancy.model_dump(mode="json")),
                        Jsonb(corpus.details[vacancy.key].model_dump(mode="json")),
                    )
                )
        conn.execute("DELETE FROM vacancy_vectors WHERE model = %s", (model,))
        with conn.cursor().copy(
            "COPY vacancy_vectors (model, key, vector) FROM STDIN"
        ) as copy:
            copy.set_types(["text", "text", "bytea"])
            for key, vector in vectors.items():
                copy.write_row((model, key, pack(vector)))
        row = conn.execute(
            "INSERT INTO corpus_published (funnel, vacancies, model) "
            "VALUES (%s, %s, %s) ON CONFLICT (id) DO UPDATE SET "
            "published_at = now(), funnel = EXCLUDED.funnel, "
            "vacancies = EXCLUDED.vacancies, model = EXCLUDED.model "
            "RETURNING published_at",
            (Jsonb(corpus.funnel.model_dump(mode="json")), len(corpus), model),
        ).fetchone()
    return Published(row["published_at"], len(corpus), len(vectors), model)


def document_vectors(corpus: Corpus, embedder: CachedEmbedder) -> dict[str, Vector]:
    """Every vacancy's vector, under the key a server will look it up by:
    the document a match embeds (the default style), as the cache keys it."""
    index = VacancyIndex.build(corpus.vacancies, corpus.details, embedder)
    model = embedder.client.settings.model
    return {
        vector_key(model, document): vector
        for document, vector in zip(index.documents, index.vectors(), strict=True)
    }


def load(database: Database) -> Corpus | None:
    """The published corpus, in its published order; None if none was."""
    with database.connect() as conn:
        meta = conn.execute("SELECT funnel FROM corpus_published").fetchone()
        if meta is None:
            return None
        rows = conn.execute(
            "SELECT record, details FROM vacancies ORDER BY position"
        ).fetchall()
    vacancies = [Vacancy.model_validate(row["record"]) for row in rows]
    details = {
        vacancy.key: VacancyDetails.model_validate(row["details"])
        for vacancy, row in zip(vacancies, rows, strict=True)
    }
    return Corpus("raw", vacancies, details, Funnel.model_validate(meta["funnel"]))


def published_model(database: Database) -> str | None:
    with database.connect() as conn:
        row = conn.execute("SELECT model FROM corpus_published").fetchone()
    return row["model"] if row else None


def published_summary(database: Database) -> tuple[datetime, int] | None:
    """When the last set was published, and how many vacancies it held."""
    with database.connect() as conn:
        row = conn.execute(
            "SELECT published_at, vacancies FROM corpus_published"
        ).fetchone()
    return (row["published_at"], row["vacancies"]) if row else None


def published_at(database: Database) -> datetime | None:
    """When the set was last published: a server reloads when this moves."""
    with database.connect() as conn:
        row = conn.execute("SELECT published_at FROM corpus_published").fetchone()
    return row["published_at"] if row else None


def seed(store: VectorStore, database: Database, model: str) -> int:
    """Copy the published vectors into a local store, so an index built on a
    fresh disk (the nightly job's) embeds only what is new. Returns how many."""
    shared = published_vectors(database, model)
    store.store(shared.load(shared.keys()))
    return shared.count()


class PublishedVectors:
    """The published vectors of one model, read once and held as bytes.

    A server reads them at start, like the corpus: about 14 MB for 1,200
    vacancies, unpacked only when a match asks for them. Read-only: the only
    writer is `publish`.
    """

    def __init__(self, blobs: dict[str, bytes]):
        self._blobs = blobs

    def load(self, keys: list[str]) -> dict[str, Vector]:
        return {key: unpack(self._blobs[key]) for key in keys if key in self._blobs}

    def store(self, vectors: dict[str, Vector]) -> None:
        raise TypeError("published vectors are written by publish(), not by a match")

    def count(self) -> int:
        return len(self._blobs)

    def keys(self) -> list[str]:
        return list(self._blobs)

    def close(self) -> None:
        pass  # shared by every match on this server


def published_vectors(database: Database, model: str) -> PublishedVectors:
    with database.connect() as conn:
        rows = conn.execute(
            "SELECT key, vector FROM vacancy_vectors WHERE model = %s", (model,)
        ).fetchall()
    return PublishedVectors({row["key"]: bytes(row["vector"]) for row in rows})


class LayeredVectors:
    """Published vectors first; everything else -- a CV's queries -- in a
    store of this server's own, where anything new is also written."""

    def __init__(self, published: VectorStore, local: VectorStore):
        self.published = published
        self.local = local

    def load(self, keys: list[str]) -> dict[str, Vector]:
        found = self.published.load(keys)
        rest = [key for key in keys if key not in found]
        return found | (self.local.load(rest) if rest else {})

    def store(self, vectors: dict[str, Vector]) -> None:
        self.local.store(vectors)

    def count(self) -> int:
        return self.published.count() + self.local.count()

    def close(self) -> None:
        self.local.close()
