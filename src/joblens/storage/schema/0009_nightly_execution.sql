-- 0009: which Cloud Run execution a nightly run is, and whether it fetched
-- one source only (7.10.2).
--
-- The admin page starts the job and stops it. To stop a run it must name the
-- execution, and the job knows its own name (Cloud Run sets
-- CLOUD_RUN_EXECUTION), so it writes it here when it starts. A run started
-- outside Cloud Run has none, and cannot be stopped from the page.
ALTER TABLE nightly_runs
    ADD COLUMN execution text,   -- "joblens-nightly-rw742"; NULL off Cloud Run
    ADD COLUMN source text;      -- --source: only this one was fetched; NULL: all
