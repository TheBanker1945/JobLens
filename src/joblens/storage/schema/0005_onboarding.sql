-- 0005: whether a person has been through (or skipped) the guide (7.7.2).
--
-- A new account starts in the guide: upload a CV, say what you want, run a
-- first match. Skipping it is allowed and must stick -- without this column a
-- person who skipped and has no CV yet would be sent back into the guide on
-- every visit. Null: not yet; a time: done or skipped, and when.
ALTER TABLE users ADD COLUMN onboarded_at timestamptz;
