-- 0003: signing in (milestone 7.5).
--
-- Invite-only: nobody signs themselves up. The owner creates an account and a
-- login link (scripts/db.py invite), sends the link however they like, and the
-- link, used once, starts a session. No passwords are ever stored.
--
-- Both tables keep a SHA-256 of the token, never the token itself: someone who
-- reads this database -- a leaked backup, a curious admin -- gets no working
-- link and no working session. The tokens are 256 random bits, so a fast hash
-- is enough; a slow one (argon2) is for passwords people chose.

-- Who may do what. The owner is unlimited on the operator's model key; a
-- tester has a budget on it (7.6), or brings their own key.
ALTER TABLE users ADD COLUMN role text NOT NULL DEFAULT 'tester'
    CHECK (role IN ('owner', 'tester'));

CREATE TABLE login_links (
    token_hash text PRIMARY KEY,
    user_id    uuid NOT NULL REFERENCES users (id) ON DELETE CASCADE,
    created_at timestamptz NOT NULL DEFAULT now(),
    expires_at timestamptz NOT NULL,
    used_at    timestamptz  -- set on first use: a link works once
);

CREATE TABLE sessions (
    token_hash text PRIMARY KEY,
    user_id    uuid NOT NULL REFERENCES users (id) ON DELETE CASCADE,
    created_at timestamptz NOT NULL DEFAULT now(),
    expires_at timestamptz NOT NULL
);
CREATE INDEX sessions_by_user ON sessions (user_id);
