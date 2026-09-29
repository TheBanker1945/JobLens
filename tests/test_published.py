"""The corpus in Postgres (7.8.1): published, read back, and ranked identically.

A hosted server has no data/raw, so the vacancies and their vectors are
published into the database. What these tests hold: what comes back is what
went in, in the same order; a new publish replaces the old one whole; and a
CV ranked from the database is ranked exactly as it is from disk -- with none
of the CV's own vectors written to the shared table.
"""

import json

import pytest
from conftest import FakeClient
from test_cv_match import PROFILE
from test_service_matching import (
    CARE,
    CORPUS,
    DATA,
    MODELS,
    SANNE,
    WISHLIST,
    CareOrData,
)

from joblens.corpus import Corpus, Funnel
from joblens.embeddings.store import CachedEmbedder, SQLiteVectors
from joblens.service import MatchRequest, rank
from joblens.storage.published import (
    LayeredVectors,
    document_vectors,
    load,
    publish,
    published_model,
    published_vectors,
)

MODEL = MODELS.embed.model
FUNNEL = Funnel(loaded=5, not_a_vacancy=1, closed=1)


def corpus_and_vectors(tmp_path, corpus=CORPUS):
    corpus = Corpus(corpus.name, corpus.vacancies, corpus.details, FUNNEL)
    embedder = CachedEmbedder(CareOrData(MODELS.embed), tmp_path / "laptop.sqlite")
    return corpus, document_vectors(corpus, embedder)


def test_what_is_published_comes_back_in_its_order(database, tmp_path):
    corpus, vectors = corpus_and_vectors(tmp_path)

    before = load(database)
    done = publish(database, corpus, vectors, MODEL)
    back = load(database)

    assert before is None  # nothing published yet
    assert (done.vacancies, done.vectors, done.model) == (3, 3, MODEL)
    assert [v.key for v in back.vacancies] == [v.key for v in corpus.vacancies]
    assert back.vacancies == corpus.vacancies
    assert back.details == corpus.details
    assert back.funnel == FUNNEL and back.name == "raw"
    assert published_model(database) == MODEL
    assert published_vectors(database, MODEL).count() == 3


def test_a_new_publish_replaces_the_old_one_whole(database, tmp_path):
    corpus, vectors = corpus_and_vectors(tmp_path)
    publish(database, corpus, vectors, MODEL)
    smaller = Corpus("raw", [CARE, DATA], CORPUS.details)
    _, fewer = corpus_and_vectors(tmp_path, smaller)

    publish(database, smaller, fewer, MODEL)

    assert [v.key for v in load(database).vacancies] == [CARE.key, DATA.key]
    assert published_vectors(database, MODEL).count() == 2  # the closed one's too


def test_a_vacancy_without_its_extraction_is_not_published(database, tmp_path):
    corpus, vectors = corpus_and_vectors(tmp_path)
    bare = Corpus("raw", corpus.vacancies, {CARE.key: CORPUS.details[CARE.key]})

    with pytest.raises(ValueError, match="not extracted"):
        publish(database, bare, vectors, MODEL)

    assert load(database) is None  # and nothing half-written


def test_a_cv_ranks_the_same_from_the_database_as_from_disk(database, tmp_path):
    """And the CV's own vectors land in the server's local file, never in the
    shared table: it has no user_id, so a deleted account could not reach them."""
    corpus, vectors = corpus_and_vectors(tmp_path)
    publish(database, corpus, vectors, MODEL)
    shared = published_vectors(database, MODEL)
    local = tmp_path / "server" / "queries.sqlite"

    def fresh():
        return FakeClient(json.dumps(PROFILE), WISHLIST)

    request = MatchRequest(cv=SANNE, top=2)
    from_disk = rank(
        request,
        corpus,
        MODELS,
        cache_dir=tmp_path / "disk",
        chat=lambda s: fresh(),
        embed=CareOrData,
    )
    from_db = rank(
        request,
        load(database),
        MODELS,
        cache_dir=tmp_path / "db",
        chat=lambda s: fresh(),
        embed=CareOrData,
        vectors=lambda model: LayeredVectors(shared, SQLiteVectors(local)),
    )

    def order(ranked):
        return [(m.vacancy.key, round(m.score, 9)) for m in ranked.ranking]

    assert order(from_db) == order(from_disk)
    assert [m.vacancy.key for m in from_db.chosen.matches] == [
        m.vacancy.key for m in from_disk.chosen.matches
    ]
    assert published_vectors(database, MODEL).count() == 3  # nothing added
    assert SQLiteVectors(local).count() > 0  # the CV's queries, kept locally
