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

## 1.3 — Structured extraction from Dutch vacancies

`extract_vacancy(text, client)` turns a vacancy into a validated `VacancyDetails`:
prompt (rules + JSON schema) -> LLM -> pydantic validation -> one repair attempt.
Five fictional samples in `data/samples/vacancies/`, each with a hard case.

**Three levels of structured output.**
1. Ask for JSON in the prompt: the model may add text, fences, or break the shape.
2. JSON mode: always valid JSON, but any shape.
3. Schema-constrained decoding: the server only lets the model pick tokens that fit
   the schema. Shape guaranteed.

**Shape is guaranteed, content is not.** Asked for "one sentence about the weather
in Zwolle" with the schema on, qwen3 had to return vacancy JSON — and invented a
salary of €2,500–3,000, "hbo" and 2 years of experience to fill it. A schema forces
the model to answer, even when the honest answer is "there is nothing here".

**The constraint does not see everything.** Ollama enforces fields, types and enum
values (including `$ref` enums), but not numeric bounds: `exclusiveMinimum: 0` did
not stop a salary of 0. Validation afterwards is still needed.

**The schema must be in the prompt too.** Constrained decoding restricts tokens; it
does not show the model the field descriptions. We put the schema in the system
prompt in both modes, so the only difference between the modes is the constraint.

**Schema vs prompt mode: identical on all 5 samples** (same output, same tokens).
With the schema in the prompt and temperature 0, qwen3 writes valid JSON anyway; the
constraint is a safety net for weaker models and messier input. Worth re-checking
per model in the 1.4 eval.

**All 10 first outputs were valid, and still had 10 content mistakes:**
- Hallucination: "schaal 11" became `salary: 4250` — from training data, not the text.
- Placeholder instead of null: "marktconform" became `salary: 0–0 per month`.
- Invented: `hours_min: 30` where the text says 40 hours.
- Heading as name: `company: "Over Fietsdeel"`.
- Nuance missed: "mbo is mooi, maar niet verplicht" became `education_level: mbo`;
  "enkele jaren ervaring" became `0`; "plaats- en tijdonafhankelijk" became `onsite`.
- Missed: Power BI (mentioned under tasks, not requirements).

**Repair loop: the feedback must say what to do.** After adding a rule that salary
must be > 0, the repair prompt "salary_min: Input should be greater than 0" gave the
same 0 back twice — the model had no acceptable alternative. Adding "if the text does
not state a value, use null" fixed it on the next try. Cost: one extra call (~2x
tokens) for that vacancy only.

**Lesson:** validation catches rule violations (0, min > max, unknown enum values),
not wrong facts. Measuring accuracy needs hand-labelled answers: milestone 1.4.

## 1.4 — An eval for extraction

`scripts/eval_extraction.py` runs every setting in `evals/extraction.toml` over the
5 samples and scores each field against hand-labelled answers in
`data/samples/expected/`. Results are saved in `evals/results/` with the git commit
that produced them.

**An eval has four parts:** a test set, ground truth written by a human, a metric,
and a comparison. Without it, every prompt change is a guess.

**Ground truth is a set of decisions.** Writing the answers surfaced 8 cases where
the text allows two readings (is "een pré" a required skill? is "af en toe thuis"
hybrid?). I decided them; the rules are in `data/samples/expected/README.md`. If the
engineer had decided alone, the eval would measure the engineer's opinions.

**Not every mistake is equal.** Each field is `correct`, `correct_null`, `wrong`,
`missed` or `hallucinated`. For job matching an invented salary is worse than a
missing one, so hallucinations are counted separately. Lists get precision, recall
and F1, so partly right counts for something.

**Scoring by rules, not by an LLM judge.** Structured fields can be compared
exactly: free, fast and reproducible. LLM-as-judge is for free text (phase 2).

**Baseline** (qwen3:8b, temperature 0, commit `528666f`):

| # | Setting        | Accuracy | Hallucinated | Wrong | Skills F1 | Out tokens | Time   |
|---|----------------|----------|--------------|-------|-----------|------------|--------|
| 1 | schema         | 86%      | 6            | 3     | 0.48      | 945        | 12 s   |
| 2 | prompt         | 86%      | 6            | 3     | 0.48      | 945        | 12 s   |
| 3 | schema + think | 97%      | 1            | 1     | 0.80      | 5,073      | 65 s   |
| 4 | prompt + think | 97%      | 1            | 1     | 0.81      | 5,167      | 63 s   |

**What the numbers say.**
- Thinking: 86% → 97%, hallucinations 6 → 1 (the invented €4,250 for "schaal 11" is
  gone), at 5.4x the tokens and time. A real trade-off, not a free win.
- Schema vs prompt mode: no difference for qwen3:8b. The constraint is a safety net.
- Mistakes that survive every setting: `company: "Over Fietsdeel"` (a heading), and
  `salary_period: "month"` without any amount — a gap in our own validation (we
  require a period when there is an amount, but not the reverse).
- Most skill errors come from rules the model was never told (split
  "Python (Django of FastAPI)", count task tools). The labelling guidelines are not
  in the prompt yet.
- Surprise: thinking made the model *stricter* about skills (dropped ONNX, TensorRT
  and "deep learning", which only appear under tasks). More reasoning is not the same
  as following your rules.

**Caution: 5 samples.** 65 scored fields per run: enough to see patterns (thinking
helps, hallucinations cluster in salary fields), not enough to rank settings that
differ by 1–2 points. Tuning the prompt on these same 5 samples risks overfitting:
more samples, and a held-out set that is never used for tuning, come first.
