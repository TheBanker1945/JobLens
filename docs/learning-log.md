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

## 1.5 — Gemini as a second provider: local vs cloud

Added Gemini through its OpenAI-compatible endpoint. The client needed no changes;
the provider table and the token accounting did.

**"Compatible" differs even per model.** On the same Gemini API:

| Model                  | thinking off with       | notes                                   |
|------------------------|-------------------------|-----------------------------------------|
| gemini-3.8-flash       | `reasoning_effort=none` | still ~330 hidden thinking tokens       |
| gemini-3.5-flash-lite  | `minimal` (or default)  | `none` → HTTP 400; thinks from `medium` |
| gemini-3.1-pro-preview | impossible              | "only works in thinking mode"           |

So profiles are now looked up per exact model, and an impossible setting raises an
error instead of silently running with thinking on. Every entry was tested with a
real call first.

**Hidden reasoning tokens.** Gemini leaves thinking out of `completion_tokens`: Pro
reported 12 output tokens for an answer that used 840 thinking tokens — all billed.
`Usage` now derives them from `total - input - output`, which is 0 on OpenAI and
Ollama (they already include reasoning) and exactly the hidden part on Gemini.

**Exact model IDs, not aliases.** `gemini-pro-latest` is not on the pricing page and
can point to a new model tomorrow; old results would then describe a different model.

**Results** (5 samples, commit `205b930`; prices from the Gemini pricing page,
updated 2026-09-16):

| # | Run                     | Acc | Halluc | Missed | Skills F1 | s/vacancy | $ / 1k vacancies |
|---|-------------------------|-----|--------|--------|-----------|-----------|------------------|
| 1 | qwen3:8b                | 86% | 6      | 0      | 0.48      | 2.6       | 0 (local)        |
| 2 | qwen3:8b +think         | 97% | 1      | 0      | 0.80      | 12.8      | 0 (local)        |
| 3 | gemini-3.8-flash        | 97% | 0      | 2      | 0.88      | 1.6       | 1.68             |
| 4 | gemini-3.8-flash +think | 94% | 0      | 4      | 0.68      | 3.3       | 4.29             |
| 5 | gemini-3.5-flash-lite   | 97% | 1      | 0      | 0.61      | 1.3       | 0.89             |
| 6 | gemini-3.1-pro +think   | 98% | 0      | 1      | 0.89      | 10.5      | 18.60            |

Total spend for this eval: $0.13.

**What the numbers say.**
- The strongest signal is hallucinations, not accuracy: Gemini Flash and Pro invented
  nothing; qwen3 without thinking invented 6 values (incl. €4,250 for "schaal 11").
  Accuracy differences of 1–3 points on 5 samples are noise.
- Gemini Flash errs on the safe side: its mistakes are "missed" (left null), not
  invented. For job matching that is the better failure direction.
- Thinking is not always better: on Gemini Flash it *lowered* accuracy and skills F1
  (more conservative: fewer skills, more nulls) at 2.5x the cost. On qwen3 it helped
  a lot. Whether reasoning helps is a per-model question, answered by the eval.
- Pro is the most accurate by 1 point, at 11x the price of Flash and 6x slower.
- Local qwen3 + thinking reaches the same accuracy as Flash for free, but at
  12.8 s per vacancy and with 1 hallucination.
- Remaining errors are mostly rules the model was never told (salary_note next to
  amounts, "hbo-denkniveau", task tools as skills) — the prompt work for 1.6.

**Conclusion.** For vacancy extraction (public data), gemini-3.8-flash with thinking
off is the best balance: 0 hallucinations, 1.6 s and $1.68 per 1,000 vacancies. Note
its price is introductory until 2026-12-31. Flash-lite is the cheaper fallback,
qwen3 + thinking the local one. CVs stay on local models by default (GDPR).

## 1.6 — Improving the prompt, and proving it with a holdout set

Added 5 new fictional vacancies (06–10) as a **holdout** split, never used for
tuning; 01–05 are the **dev** split. Then: measure before → change the prompt using
dev mistakes only → measure after. `--mistakes` only shows dev mistakes and
`--split dev` runs dev alone, so the holdout stays clean.

**Changes** (all derived from dev):
- The 1.4 labelling guidelines in the field descriptions (which skills count and how to
  split them, either/or languages, salary_note, work_mode, vague terms → null).
- General rules in the system prompt: never fill a value from your own knowledge
  (a pay scale is not an amount); read the task description for skills too.
- Validation: a `salary_period` without an amount is now invalid.

**Results** (before: commit `134c632`; after: commit `e5735d3`):

| Run              | Dev before → after | Holdout before → after             |
|------------------|--------------------|------------------------------------|
| qwen3:8b         | 86% → 98%, skills F1 0.48 → 0.91, invented 6 → 1 | 86% → 88%, skills F1 0.30 → 0.48, languages F1 0.73 → 1.00 |
| qwen3:8b +think  | 97% → 80% (1 loop) | 92% → 89%, invented 1 → 3          |
| gemini-3.8-flash | 95% → 100%, skills F1 0.88 → 0.95 | 95% → 95%, skills F1 0.63 → 0.66 |
| gemini-3.5-flash-lite | 98% → 98%, skills F1 0.61 → 0.97 | 97% → 95%, skills F1 0.63 → 0.62 |

**What this shows.**
- **Overfitting to a prompt is real.** Dev jumped (qwen3 86% → 98%); the holdout barely
  moved (86% → 88%). The rules fixed *these* 5 vacancies much more than vacancies in
  general. Without a holdout I would have reported "+12 points".
- **What did generalise:** skills for qwen3 (holdout F1 +0.18) and language names
  (0.73 → 1.00). Rules that describe a general *kind* of mistake transfer; rules that
  describe one sample's wording mostly don't.
- **Run-to-run noise is ~2 points.** Gemini at temperature 0 is not fully
  deterministic (gemini-3.8-flash dev: 97% in 1.5, 95% in the 1.6 baseline, same
  prompt). Differences smaller than that mean nothing.
- **A prompt change can break another field.** "Keep the wording of the text" for
  skills made qwen3 write "nederlands" instead of "Dutch" (languages F1 0.80 → 0.20
  on dev). Only per-field scores revealed it; fixed by scoping the rule.
- **Data leakage through guidelines.** Rules decided while labelling the holdout
  (remote → city null, "up to €85k" → min null, ZZP → freelance) were deliberately
  kept out of the prompt: putting them in would raise the holdout score with leaked
  knowledge. The remaining holdout errors (city: 2–3/5) are exactly those cases.
  Adding them now is fine for the product, but then the holdout is "used up" and a
  fresh one is needed to measure honestly.
- **qwen3 + thinking at temperature 0 loops.** Greedy decoding with reasoning can
  repeat forever; one looping request blocked Ollama (one request at a time) until
  the client timed out. Fixed with an output cap (`max_tokens=3000`) and failing fast
  on `finish_reason: "length"`. Qwen advises against greedy decoding in thinking
  mode; with the longer prompt it is now worse than qwen3 without thinking.

**Conclusion.** gemini-3.8-flash stays the default (100% dev, 95% holdout, 1 invented
value on 10 vacancies). qwen3:8b without thinking is the better local fallback now.
Next lever is more (and more varied) samples, not more prompt rules.

## 2.1 — Embeddings and semantic search by hand

`scripts/search_vacancies.py "query"` ranks the 10 sample vacancies by meaning, using
a local embedding model (`qwen3-embedding:0.6b` on Ollama) and cosine similarity
written out in pure Python (`joblens.embeddings.similarity`).

**An embedding** is a list of numbers (1,024 here) that places a text in a "meaning
space": similar meaning → nearby vectors. **Cosine similarity** measures the angle
between two vectors: dot product divided by both lengths. 1 = same direction,
0 = unrelated. qwen3-embedding already returns vectors of length 1, so cosine equals
the plain dot product.

**Scores are relative.** 0.60 is not "60% match"; it only means "closer than 0.50".
Different models produce different score ranges, so thresholds don't transfer.

**Same model on both sides.** Each model has its own coordinate system; vectors from
different models can't be compared (the code refuses vectors of different sizes). Because
CVs stay local (GDPR), the model used for CV-to-vacancy matching must be local too.
Settings now have a prefix per task (`LLM_*`, `EMBED_*`), so embeddings can stay local
while extraction uses Gemini.

**Asymmetric search.** A short query and a long vacancy are different kinds of text.
Qwen3-Embedding expects queries to carry a task instruction
(`Instruct: …\nQuery: …`); documents are embedded as they are. Without it,
"Python-ontwikkelaar gezocht in Amsterdam" was closer to a Dutch *orderpicker* sentence
(0.637) than to an English backend-developer sentence (0.545): the shared Dutch
sentence pattern ("[job] in [city]") outweighed the meaning. With the instruction the
ranking was right.

**Example searches** (10 vacancies, embedded in <1 s):

| Query | #1 result | Note |
|-------|-----------|------|
| "werken met je handen, geen diploma nodig" | Orderpicker (0.433) | no shared words — pure meaning |
| "zorg voor ouderen" | Verpleegkundige Geriatrie (0.596) | next one 0.367: clear margin |
| "python developer" | Backend Developer (0.580) | next one 0.445 |
| "python baan in amsterdam" | Product Manager (0.605) ✗ | backend developer only #3 |

**Dilution.** The last search fails because an embedding summarises the *whole*
vacancy: "Python" is one word in a long text about bike sharing, Scrum and holidays,
while "Amsterdam" and generic job wording match the product manager's text. Hypothesis
for 2.2: embed a focused text (title + skills + city from the extraction) instead of
the raw vacancy — measured with a retrieval eval, not by eye.

**SDK detail:** the openai SDK asks for embeddings as base64 (`encoding_format`)
instead of a JSON list of numbers — about 4x less data — and decodes them itself.
