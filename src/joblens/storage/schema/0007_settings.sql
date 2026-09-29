-- 0007: switches the owner sets from the settings page (7.8.5).
--
-- One row per switch. The first is whether the nightly job fetches vacancies
-- ("nightly_fetch", {"enabled": true|false}). A missing row is the default,
-- which is off: a new database fetches nothing until the owner says so.
-- These are the app's settings, not anybody's data: no user_id.
CREATE TABLE app_settings (
    key        text PRIMARY KEY,
    value      jsonb NOT NULL,
    updated_at timestamptz NOT NULL DEFAULT now()
);
