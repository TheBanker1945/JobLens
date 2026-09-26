-- 0001: people, their CVs, and what JobLens stored for them (milestone 7.2).
--
-- Every table but `users` belongs to a user and says so with a foreign key
-- that ends in ON DELETE CASCADE. That one clause is "delete my data": removing
-- a person's row removes their CVs, files, runs, labels and preferences with
-- it, in one statement, with nothing left behind for a forgotten table.
--
-- What is NOT here: the vacancies, their extracted fields and embeddings. They
-- are shared by everybody, written by the nightly fetch, and still live in
-- data/raw/ and data/cache/; moving them is part of hosting (7.8).

CREATE TABLE users (
    id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    email        text UNIQUE,  -- null until 7.5 signs people in
    display_name text,
    locale       text,         -- en, nl, de, fr or es; null until chosen
    created_at   timestamptz NOT NULL DEFAULT now()
);

-- One active CV per person, plus every one they had before it.
CREATE TABLE cvs (
    id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id     uuid NOT NULL REFERENCES users (id) ON DELETE CASCADE,
    name        text NOT NULL,     -- the file stem: what runs and labels call it
    filename    text NOT NULL,
    strip_name  text,              -- the name redaction removed, to read it again the same way
    text        text NOT NULL,     -- redacted: the only version that is sent anywhere
    digest      text NOT NULL,     -- of `text`, the same digest a run is stamped with
    profile     jsonb,             -- what a model made of it (cv/schema.py)
    uploaded_at timestamptz NOT NULL DEFAULT now(),
    active      boolean NOT NULL DEFAULT false
);
-- "One active" is a rule the database keeps, not one the code hopes for: a
-- second active row for the same person is refused, whoever tries to write it.
CREATE UNIQUE INDEX cvs_one_active_per_user ON cvs (user_id) WHERE active;
CREATE INDEX cvs_newest_first ON cvs (user_id, uploaded_at DESC);

-- The uploaded file itself, unredacted, kept only so that a better reader can
-- read it again. A row with an expiry rather than a column on `cvs`, so that
-- deleting the file is deleting a row and the CV's text and history stay.
CREATE TABLE cv_files (
    cv_id      uuid PRIMARY KEY REFERENCES cvs (id) ON DELETE CASCADE,
    data       bytea NOT NULL,
    expires_at timestamptz NOT NULL
);
CREATE INDEX cv_files_by_expiry ON cv_files (expires_at);

-- A run is a document (cv/runs.py RunRecord, ~70 KB), so it is stored as one,
-- in `record`. The columns beside it are the few a list of runs needs, so that
-- listing twenty runs does not mean reading 1.4 MB of JSON.
CREATE TABLE runs (
    user_id     uuid NOT NULL REFERENCES users (id) ON DELETE CASCADE,
    -- "2026-09-25_1636_lisa_de_vries", as FileStore names it. COLLATE "C" sorts
    -- it byte by byte, as Python does, so a list of runs comes back in the same
    -- order from either store (the default collation skips the "-" in "-2").
    id          text COLLATE "C" NOT NULL,
    cv_name     text NOT NULL,
    at          timestamp NOT NULL,  -- the run's own stamp, as it wrote it (no time zone)
    corpus      text NOT NULL,
    corpus_size integer NOT NULL,
    judged      integer NOT NULL,
    ranked      integer NOT NULL,
    outcome     text NOT NULL,
    cost_usd    double precision,
    record      jsonb NOT NULL,
    PRIMARY KEY (user_id, id)
);
CREATE INDEX runs_newest_first ON runs (user_id, at DESC, id DESC);

-- What one person said about one CV's vacancies (evals/matching.py CVLabels),
-- with every reason in it, as one document.
CREATE TABLE labels (
    user_id    uuid NOT NULL REFERENCES users (id) ON DELETE CASCADE,
    cv         text COLLATE "C" NOT NULL,
    labels     jsonb NOT NULL,
    updated_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (user_id, cv)
);

-- What a person wants from their next job. One set per person, not per CV: a
-- CV is facts about the past, preferences are constraints on the future.
CREATE TABLE preferences (
    user_id    uuid PRIMARY KEY REFERENCES users (id) ON DELETE CASCADE,
    answers    jsonb NOT NULL,
    updated_at timestamptz NOT NULL DEFAULT now()
);
