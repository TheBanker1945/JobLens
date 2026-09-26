-- 0002: background jobs, and what redaction took out of a CV (milestone 7.4).

-- A match takes 20 to 60 seconds; a web request cannot wait that long. The
-- request starts a job and returns at once, the job writes its progress here,
-- and the page reads it back. In the database rather than in the server's
-- memory, so any server process can answer "how far is it", and a restart
-- leaves a record that says the job was interrupted instead of nothing.
CREATE TABLE jobs (
    id         uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id    uuid NOT NULL REFERENCES users (id) ON DELETE CASCADE,
    kind       text NOT NULL,              -- 'match'
    status     text NOT NULL DEFAULT 'queued'
               CHECK (status IN ('queued', 'running', 'done', 'failed')),
    stage      text,                       -- reading, ranking, judging
    done       integer NOT NULL DEFAULT 0, -- vacancies judged so far
    total      integer NOT NULL DEFAULT 0,
    request    jsonb NOT NULL,             -- what was asked for
    run_id     text,                       -- the stored run, when done
    error      text,                       -- the sentence to show, when failed
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);
-- One open job per person: a second click on "match" while one is running
-- would pay for the same run twice. The database refuses it, not the page.
CREATE UNIQUE INDEX jobs_one_open_per_user ON jobs (user_id)
    WHERE status IN ('queued', 'running');
CREATE INDEX jobs_newest_first ON jobs (user_id, created_at DESC);

-- How many of each kind of detail redaction removed (cv/clean.py): e-mail,
-- phone, address... Counts only, never the removed values -- those are the
-- personal data the redaction exists to keep out.
ALTER TABLE cvs ADD COLUMN removed jsonb NOT NULL DEFAULT '{}';
