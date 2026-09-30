-- 0008: what each nightly run did, for the owner's admin page (7.10.1).
--
-- The nightly job keeps the vacancy state in files in a bucket, which the web
-- app cannot read (its service account reads three secrets and nothing else).
-- So the job writes here what the page shows: a row when a run starts, filled
-- in when it ends, with the sources as it left them (sources/overview.py).
--
-- Public facts about adverts and about the job itself, not anybody's data: no
-- user_id. The job deletes rows older than 90 days when it starts a run.
CREATE TABLE nightly_runs (
    id            bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    started_at    timestamptz NOT NULL DEFAULT now(),
    finished_at   timestamptz,   -- NULL: still going, or cut off
    -- 'schedule': Cloud Scheduler; 'hand': --force; 'page': the admin page (7.10.2)
    trigger       text NOT NULL CHECK (trigger IN ('schedule', 'hand', 'page')),
    fetched       boolean NOT NULL,   -- false: --no-fetch, index and publish only
    steps         jsonb NOT NULL DEFAULT '{}',   -- {"fetch": true, "index": true, ...}
    new_vacancies integer,   -- stored for the first time by this run's fetch
    problems      jsonb NOT NULL DEFAULT '[]',   -- what its fetch report says needs a look
    in_joblens    integer,   -- what a match ranks once it was done
    overview      jsonb      -- per source and board, as it left them
);
CREATE INDEX nightly_runs_newest ON nightly_runs (started_at DESC);
