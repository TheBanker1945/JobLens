-- 0006: the vacancies a match ranks, and their embeddings (7.8.1).
--
-- Until now a server read them from its own disk: data/raw (the adverts, what
-- extraction read, which are still open) and data/cache (the vectors). A
-- hosted container has no lasting disk, so the open, extracted vacancies are
-- published here by scripts/publish_corpus.py, and a server with
-- `--corpus db` reads them at start.
--
-- These are public adverts, not anybody's data: no user_id, and deleting an
-- account does not touch them. Nothing derived from a CV is ever written
-- here -- a CV's own vectors stay in the server's local, temporary cache.

-- One row per vacancy a match may rank: open, extracted, a real job, no
-- duplicate. `record` is the Vacancy and `details` its extraction, both as
-- the file stores keep them, so the loader is a model_validate away.
-- `position` keeps the file corpus's order: two vacancies can tie on score,
-- and a ranking read from here must break ties the way one read from disk does.
CREATE TABLE vacancies (
    key      text PRIMARY KEY,   -- Vacancy.key: "source:source_id"
    position integer NOT NULL UNIQUE,
    record   jsonb NOT NULL,
    details  jsonb NOT NULL
);

-- A vector for each vacancy's document, keyed exactly as the local cache keys
-- it (embeddings/store.py: sha256 of the model and the document text), stored
-- the same way (float32 bytes). A vector copied from a laptop's cache is found
-- here under the same key, and a changed document gets a new key rather than
-- an old vector. Searching happens in Python (two queries fused by position,
-- a cap per employer), so a plain column does; pgvector would add an extension
-- for nothing yet.
CREATE TABLE vacancy_vectors (
    model  text NOT NULL,
    key    text NOT NULL,
    vector bytea NOT NULL,
    PRIMARY KEY (model, key)
);

-- When the set above was published, and what it left out on the way (the
-- funnel a run records: loaded, not a vacancy, closed, duplicates, not
-- extracted). One row: a publish replaces it.
CREATE TABLE corpus_published (
    id           boolean PRIMARY KEY DEFAULT true CHECK (id),
    published_at timestamptz NOT NULL DEFAULT now(),
    funnel       jsonb NOT NULL,
    vacancies    integer NOT NULL,
    model        text NOT NULL   -- whose vectors a match should look for
);
