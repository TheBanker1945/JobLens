-- 0004: a person's own AI provider, and what every paid call cost (7.6).

-- The model that reads a person's CV and judges their matches, on their key.
-- One per person. The key is encrypted by the server (joblens/vault.py) before
-- it is written, so this column holds ciphertext: a reader of the database
-- without the server's secret has no key. `key_hint` is the last four
-- characters, so a settings page can say which key is in use without showing it.
-- `provider` is one of a fixed list (llm/presets.py), never a free address:
-- a server that calls whatever URL a user types can be pointed at its own
-- network.
CREATE TABLE provider_keys (
    user_id     uuid PRIMARY KEY REFERENCES users (id) ON DELETE CASCADE,
    provider    text NOT NULL,
    model       text NOT NULL,
    thinking    boolean NOT NULL DEFAULT false,
    key_secret  bytea NOT NULL,
    key_hint    text NOT NULL,
    verified_at timestamptz NOT NULL,  -- when a test call last succeeded
    updated_at  timestamptz NOT NULL DEFAULT now()
);

-- Every paid model call a person caused, what it cost, and whose key paid.
-- "operator" is JobLens's key (Mahdi's): testers have a monthly budget on it,
-- and all testers together a monthly cap. "own" is the person's own key:
-- unlimited, and recorded so they can see what JobLens cost them.
CREATE TABLE usage (
    id            bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    user_id       uuid NOT NULL REFERENCES users (id) ON DELETE CASCADE,
    at            timestamptz NOT NULL DEFAULT now(),
    kind          text NOT NULL,       -- 'cv' (reading an upload), 'match'
    model         text NOT NULL,
    prompt_tokens integer NOT NULL,
    output_tokens integer NOT NULL,
    cost_usd      double precision,    -- null when the model has no known price
    paid_by       text NOT NULL CHECK (paid_by IN ('operator', 'own'))
);
CREATE INDEX usage_by_user_and_month ON usage (user_id, at);
CREATE INDEX usage_operator_by_month ON usage (at) WHERE paid_by = 'operator';
