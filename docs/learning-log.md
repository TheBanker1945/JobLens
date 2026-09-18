# Learning Log

Notes on what I learned while building JobLens, one entry per milestone.

## 0 — Foundation

Set up the uv package (`src/` layout), `.gitignore`, `.env.example`, license and a
smoke test. `pyproject.toml` says what the project wants (dependencies as ranges);
`uv.lock` records exactly what was installed, so every machine gets the same versions.

## 1.1 — First LLM call by hand

Built `joblens.llm.raw_client.chat()`: one HTTP POST with httpx, no SDK.

**An LLM call is just HTTP + JSON.** Every OpenAI-compatible provider accepts
`POST {base_url}/chat/completions` with a model name and a list of messages, and
replies with the answer in `choices[0].message.content`.

**Messages and roles.** `system` sets the behaviour ("answer briefly, in Dutch"),
`user` is the question, `assistant` is a previous model reply.

**The API is stateless.** The model remembers nothing between calls. A "conversation"
is the client sending the whole message history again on every call — which also
means longer chats cost more input tokens each turn.

**Tokens.** Models read and write tokens (word pieces), and providers bill per token.
`usage` splits them into `prompt_tokens` (input) and `completion_tokens` (output).

**Reasoning ("thinking").** qwen3 can think before answering. The thinking comes back
in a separate `reasoning` field (DeepSeek calls it `reasoning_content`) and counts as
output tokens. On Ollama, `reasoning_effort: "none"` switches it off.

Measured with `scripts/raw_call.py "Wat is een vacature?"` (qwen3:8b, warm model):

| Thinking | Output tokens | Latency |
|----------|---------------|---------|
| off      | 23            | ~0.27 s |
| on       | 207           | ~2.3 s  |

Same quality of answer for a simple question, ~9x the tokens and time. For extraction
we keep thinking off.

**Temperature.** At 0 the model always picks the most likely next token, so the same
prompt gives the same answer (two runs: identical 23-token replies). Extraction wants
that predictability.

**Other observations.**
- The first call after ~5 idle minutes took 6.6 s: Ollama had unloaded the model and
  had to load it again (cold start).
- Thinking off added 6 hidden input tokens (54 vs 48) — the chat template changes the
  prompt behind the scenes.

## 1.2 — The openai SDK behind our own interface

Added `joblens.llm.client.LLMClient` (openai SDK) next to the hand-built
`raw_client`, both returning the same `ChatResult`. The rest of JobLens depends on the
`ChatClient` interface in `llm/types.py`, not on a specific client.

**Same wire format.** A test proves the SDK sends exactly the same JSON body as the
raw client. The SDK is a convenience layer, not a different protocol.

**What the SDK adds.**
- Retries with backoff on connection errors, 429 (rate limit) and 5xx — but not on
  401: a wrong key never gets better by retrying (both are tested).
- Named errors: `RateLimitError`, `AuthenticationError`, `APIConnectionError`, ...
- Typed responses and a reusable connection pool.

**What it costs.**
- Its types only know OpenAI's own fields. Ollama's `reasoning` field arrives in
  `message.model_extra` — the main trap of "OpenAI-compatible".
- Default timeout is 600 s; we set 120 s.
- It uses `httpx2` internally, not the `httpx` we depend on.

**Speed** (`scripts/compare_clients.py`, qwen3:8b, warm, median of 5):

| Client | Thinking off | Thinking on |
|--------|--------------|-------------|
| raw    | 0.26 s       | 2.64 s      |
| SDK    | 0.26 s       | 2.52 s      |

The client overhead is invisible; generating tokens is what takes time.
At temperature 0 all 10 calls gave the identical answer.

**"Compatible" is not "identical".** Every provider switches thinking in its own way,
so `llm/providers.py` holds a table of verified profiles. With no thinking field,
qwen3 thinks by default: 271 output tokens to say "Hallo." versus 3 with thinking off.
A wrong mapping fails silently (thinking stays on and costs tokens), so a provider
only gets a profile after a real test call. Unknown providers get a warning and their
own default.
