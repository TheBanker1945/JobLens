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

## 2.2 — A retrieval eval: what should we embed?

`scripts/eval_retrieval.py` measures search quality over 15 queries (10 dev, 5
holdout) against the 10 sample vacancies, for 8 variants: 4 text styles × local and
Gemini embedding models.

**Metrics.**
- **hit@1**: was the first result relevant? What the user sees without scrolling.
- **recall@3**: what share of the relevant vacancies is in the top 3? With 2
  relevant vacancies, finding one scores 0.5.
- **MRR**: 1 / position of the first relevant result (1st = 1.0, 2nd = 0.5, none = 0).
  Rewards ranking higher, not just appearing.

**Results** (commit `6d697be`):

| Variant                          | dev hit@1 / MRR | holdout hit@1 / MRR |
|----------------------------------|-----------------|---------------------|
| local raw                        | 90% / 0.95      | 80% / 0.84          |
| local structured                 | 80% / 0.84      | 80% / 0.90          |
| local title only                 | 50% / 0.61      | 80% / 0.90          |
| local structured, no instruction | 70% / 0.78      | 80% / 0.90          |
| local structured + raw           | 80% / 0.88      | 80% / 0.87          |
| gemini-001 structured            | 90% / 0.93      | 80% / 0.87          |
| gemini-2 structured              | 100% / 1.00     | 60% / 0.77          |
| **gemini-2 raw**                 | **100% / 1.00** | **100% / 1.00**     |

**The hypothesis from 2.1 was wrong.** Embedding a structured summary (title, city,
skills, …) is *worse* than the raw text, not better. The failing dev queries show
why: "freelance opdracht als **zzp'er**" and "werk **zonder diploma**" are words in
the vacancy text, but extraction turns them into `contract_type: freelance` and
`education_level: null`. **Extraction is lossy: a summary drops the very vocabulary
people search with.** Adding the raw text back (structured + raw) did not beat raw
either — the summary mostly adds noise for retrieval.

Structured fields are still valuable, but for **filters** ("max 32 hours", "hbo"),
not for the embedding. Filtering and semantic search are different jobs.

**Gemini embeddings beat the local model** (gemini-2 raw: perfect on both splits).
But CV matching needs *both* sides in the same vector space and CVs stay local, so
the local model remains the one for matching. Gemini is an option for
vacancy-only search.

**The query instruction is worth ~1 query** on dev (hit@1 70% → 80%, MRR 0.78 →
0.84); only qwen3-embedding has one.

**Noise warning.** 10 dev queries means one query = 10 points; 5 holdout queries
means one query = 20 points. gemini-2 structured scoring 100% on dev and 60% on
holdout is mostly that. Only the big gaps (title-only vs raw, local vs gemini-2 raw)
are meaningful.

**numpy** now does the ranking: normalise all vectors to length 1, then one matrix
multiply gives every query-document cosine. Measured against the hand-written pure
Python version (which stays, and which a test uses as the reference):

| documents | pure python | numpy | speed-up |
|-----------|-------------|-------|----------|
| 1,000     | 235 ms      | 37 ms | 6.4x     |
| 10,000    | 2,255 ms    | 294 ms| 7.7x     |

Most of numpy's time goes into turning Python lists into arrays; a real system keeps
the matrix in memory, where the gap is much bigger.

**Embeddings are cached** on disk per (model, text) in `data/cache/` (gitignored), so
repeated runs cost no time or money.

**Another "OpenAI-compatible" difference:** Gemini leaves the `index` field empty in
embedding responses and relies on the order of the items; OpenAI and Ollama number
them. The client now handles both.

## 2.3 — Real vacancies: three sources behind one interface

`scripts/fetch_vacancies.py` fetches real Dutch vacancies into `data/raw/`
(never committed) from three public sources, all without a key, registration or
trial:

| Source | Shape | Note |
|--------|-------|------|
| Recruitee | one company board per request | text is split over `description` **and** `requirements` |
| Greenhouse | one company board per request | `content` is HTML-escaped one extra time (`&lt;p&gt;`) |
| jobdataapi | aggregator: many employers, one endpoint | anonymous tier, ~10 requests/hour per IP |

Every adapter returns the same `Vacancy`, so extraction, search and matching never
know where a vacancy came from. Adding a source is one file plus a line in
`sources.toml`.

**Rate limits, measured rather than assumed.** A controlled probe of jobdataapi
(stopping at the first 429) showed: the limit is counted **per IP** (no key is
involved), the window is about an hour, `Retry-After` says exactly how long to wait
(we saw 2710 s), and **rejected requests still count** — 10 responses with HTTP 403
used up the whole allowance. So the client never retries, stops at the first 429 and
varies its filters so every request returns different vacancies. Working around the
limit (for example by rotating IPs) is off the table: the limit *is* the free tier,
and this is a public portfolio repo.

**Privacy before storage.** Vacancies name recruiters with emails and phone numbers:
personal data, exactly like a CV. `clean.py` removes them before anything is written
to disk (7 of 88 vacancies contained some). The phone pattern is deliberately narrow,
so "3200 - 3800" and "2026" survive.

**Licence, and why the fictional samples stay.** Vacancy texts are someone else's
copyright, so `data/raw/` is gitignored. The committed test set stays fictional:
anyone can clone the repo and run the evals, while the real set stays local and
honest. Public reproducibility and real measurement, without mixing the two.

**What real vacancies look like** (88 fetched from 6 company boards):

| | fictional samples | real |
|---|---|---|
| length | 790–1,000 chars | median 5,463, max 10,430 |
| language | all Dutch | 73 of 88 English |
| location | one city | "EMEA; Germany; London, United Kingdom; Paris, France; Remote - Netherlands" |

Three consequences for 2.4: extraction has only ever been tested on texts 5x
shorter; company boards on Recruitee and Greenhouse skew tech and English, so the
aggregator matters for sector variety; and `city` sometimes holds a list of
countries instead of a city.

**Storage format:** JSON Lines, one file per source, one vacancy per line. Easy to
append, easy to stream back, and a half-written line never corrupts the rest.
Re-fetching skips what is already stored (dedupe on source + id).

## 2.4 — Scraping Indeed and LinkedIn, and making real vacancies searchable

Two things at once: two boards that publish no feed, and the bridge that was
missing since 2.3 — fetched vacancies went into `data/raw/` and nothing read them
back out. Search still only knew the ten fictional samples.

**A dependency with a trap.** JobSpy knows the endpoints Indeed and LinkedIn use
and follows their changes, which is worth more than writing that by hand. But its
last release (1.1.82, July 2025) requires `numpy==1.26.3`, and this project is on
numpy 2. `uv add python-jobspy` does not fail: it quietly resolves back to
**python-jobspy 1.1.13 from 2024**. The fix is a git commit pin (`fda080a`, where
the requirement is `numpy>=1.26.0`) in an optional `scrape` group, so the library,
the tests and the evals stay on five dependencies.

**Scraped sources fail differently from API sources.** Reading JobSpy's code first
was worth more than reading its README:

| What it does | What we do instead |
|---|---|
| One request per LinkedIn description, no pause, `except: return {}` | Ask for the listing only, fetch descriptions ourselves |
| Indeed page loop with no guard (it can run forever) | Every scrape runs under a deadline, in a daemon thread |
| `distance` in miles, default 50 | `distance_km` in config, converted |
| Harvests recruiter e-mails into a column | Dropped, along with the un-redacted description |

The LinkedIn half is two phases now: JobSpy lists the jobs (paced 3–7 s per page),
and we fetch each description from the guest fragment endpoint ourselves. That
inverts the risk — the request path that gets an IP throttled is the one we
control. Jobs already in the store cost no request at all, a run is capped, and a
job whose description does not arrive is **dropped rather than stored**: in the
store an empty vacancy is indistinguishable from a real one, and the matcher would
happily rank it. Above 30% missing descriptions the run stops and calls itself
throttled. LinkedIn also answers our honest `JobLens/0.1` User-Agent with HTTP 200,
so nothing here pretends to be a browser.

**"Nothing stored" says nothing.** A quiet week, a dead API and a throttled board
all look the same from the outside. Measured, not assumed: `monteur in Eindhoven`
returned zero jobs with `hours_old=72` and ten with `hours_old=336` — a real zero.
So the rules in `sources/report.py` treat *one* empty search as normal, and only a
whole source that listed nothing, or a source whose jobs mostly arrive without a
description, as a problem. Every run writes `data/raw/runs/<timestamp>_fetch.json`
and exits non-zero when it needs a look, which is what makes cron speak up.

**A privacy hole from 2.3, found while adding the new sources.** `Vacancy.raw`
kept the payload as received — including the description in HTML, with the
recruiter's e-mail still in it. `text` was redacted; the same address went to disk
one field along, in 7 of 29 Recruitee vacancies. `clean.redact()` now walks a
payload and strips contact details from every string. The claim in 2.3 that
contact details never reach disk was true of `text` only.

**Cross-source duplicates.** One job is advertised on Indeed, on LinkedIn and on
the company's own board. The store now also skips a job it already has from
another source, matched on title + company + city, normalised. Strict on purpose:
missing a duplicate costs one row, merging two jobs that only look alike loses a
real one. The first full run skipped 6.

**The bridge.** `scripts/index_vacancies.py` extracts every stored vacancy (cached
per vacancy, append-only, last line wins) and embeds the document for it;
`search_vacancies.py --corpus raw` then searches the real set. 202 vacancies,
no extraction failures, about **$0.55 in total**, and the second run costs nothing:
every extraction and every vector comes from the cache.

**How much of a vacancy does an embedding model actually read?** The question that
mattered most, and the answer was not the one on the model card. Each model was
probed by appending a distinctive sentence to texts of growing length: if the
vector does not move at all, the tail was cut.

| model | window it really uses | truncates past | our 202 documents |
|---|---|---|---|
| `gemini-embedding-001` | ~2,048 tokens | ~10,500 chars | 2 would be cut |
| `qwen3-embedding:0.6b` | **4,096** (the model itself: 32,768) | ~22,000 chars | none |
| `gemini-embedding-2` | ~8,192 tokens | ~42,000 chars | none |

All three truncate **silently**: HTTP 200, no warning. Ollama serves qwen3 with a
4,096-token window regardless of what the model supports, and `prompt_tokens`
stops at 4095 whether the input is 24,000 characters or 80,000. Vacancy text runs
at about 5.1–5.4 characters per token across all three models.

Three consequences. Our longest document (11,526 chars) fits the local model with
room to spare, so nothing is truncated today. The cloud option that looked
appealing in 2.2, `gemini-embedding-001`, has a *smaller* window than the local
model and would already cut two of our vacancies — worth knowing before the
retrieval eval is ever re-run over real vacancies. And `structured_raw` earns its
place as the default for a second reason: the summary sits at the front, so if a
document ever is cut, the decisive facts survive. `VacancyIndex.oversized()` now
names any document that comes close.

**Scheduling.** `scripts/daily_update.sh` fetches, then indexes, logs to
`data/raw/logs/` and prints nothing unless something went wrong — cron mails what
a job prints, so silence is the success signal. On WSL, cron only runs while WSL
does, so Windows Task Scheduler is the more reliable trigger.
`scripts/data_status.py` answers the question a demo depends on: how fresh is this,
and did the last run go well?

## 3.1 — The retrieval eval on 202 real vacancies: every 2.2 answer changed

Milestone 2.2 chose what to embed using ten vacancies I wrote myself. This re-runs
the same eval over the 202 real ones, and almost every conclusion flips. The
fictional set was not a small version of the real thing; it was a different thing.

**The eval now takes a corpus.** `joblens.corpus` loads `samples` or `raw` into one
`Vacancy` type, and `search_vacancies.py` and `eval_retrieval.py` share it — search
and the measurement of search have to see the same vacancies. Vacancies are
identified by `Vacancy.key` everywhere, so one query-file format fits both corpora.

**23 labelled queries** (15 dev, 8 holdout) in `evals/queries/raw.json`, with the
rules behind them in the README there and every decision in `raw-review.md`.
**Labelled by Claude, not by me** — worth remembering before quoting these numbers.
Candidates were **pooled**: the union of the top 10 of all eight variants, 607
judgements. Judging only what today's default retrieves would score a better
variant wrong for finding something nobody was asked about.

**Results** (`evals/results/2026-09-21_1510_retrieval_raw.json`):

| Variant | dev hit@1 / MRR | holdout hit@1 / MRR |
|---|---|---|
| local raw | 80% / 0.85 | 75% / 0.88 |
| local structured | 67% / 0.73 | 62% / 0.75 |
| local title only | 67% / 0.70 | 62% / 0.74 |
| local structured, no instruction | 40% / 0.57 | 50% / 0.63 |
| gemini-001 structured | 73% / 0.80 | 75% / 0.88 |
| **gemini-2 structured** | **87% / 0.90** | **88% / 0.91** |
| gemini-2 raw | 60% / 0.71 | 38% / 0.54 |
| local structured + raw | 80% / 0.84 | 62% / 0.79 |

**`gemini-2 raw` went from best to worst.** It scored a perfect 100% / 1.00 on both
splits of the fictional set. On real vacancies it is the weakest variant in the
table, below every local one. Had we trusted 2.2 and shipped it, search would have
got worse and the sample eval would still have said it was perfect.

**Why: the fictional vacancies had no boilerplate and no junk.**

*Boilerplate.* Twenty-nine Adyen vacancies all open with the same 600 words. Under
`raw` a company's vacancies sit far closer together than two vacancies picked at
random (Adyen +0.225 above the baseline cosine for gemini-2, +0.325 for the local
model); the structured summary roughly halves that (+0.123). Embedding the raw text
partly embeds *the employer*, not the job. Ten vacancies from ten invented
companies could not show this.

*Junk.* `recruitee:396939` and `recruitee:312680` are open-application pages — "tell
us who you are and what you're looking for". That is not a vacancy, it is a
*query*, which is exactly why it embeds so close to one. They were left in
deliberately, to price them:

| Variant | ranked #1 | in top 10 | hit@1 → without them |
|---|---|---|---|
| local structured, no instruction | 12 / 23 | 21 / 23 | 43% → **65%** |
| gemini-2 raw | 5 / 23 | 21 / 23 | 52% → 61% |
| gemini-2 structured | 0 | 0 | 87% → 87% |
| local raw | 0 | 0 | 78% → 78% |

Two documents out of 202 cost the worst variant **22 points of hit@1**. The
structured styles are largely immune because neither page has extracted fields to
summarise — so the structured document is nearly empty, and an empty document
attracts nothing.

**recall@3 is no longer readable and recall@10 replaces it.** With 202 vacancies a
broad query has more than three right answers: "logistiek medewerker magazijn" has
seven, so recall@3 caps at 43% however good the ranking is. On ten vacancies
recall@10 was 100% for every variant — it measured nothing there and does the work
here.

**The query instruction is worth far more than 2.2 suggested**: local structured
drops from 67% to 40% hit@1 without it (2.2 said 80% → 70%). Most of that gap is
the junk pages, which the instruction keeps off the top spot.

**What still holds from 2.2.** Raw text beats a structured summary *for the local
model* (80% vs 67%), the lossy-extraction argument intact. What does not hold is
that this generalises: for `gemini-2` the ordering reverses, structured 87% against
raw 60%. "Which style is best" turns out to be a property of the model, not of the
data, which is not something the fictional set could ever have told us.

**Noise.** 15 dev queries means one query is 6.7 points, 8 holdout queries 12.5
points. The gaps that survive that: gemini-2 structured over everything (both
splits), gemini-2 raw and no-instruction at the bottom (both splits). The middle of
the table is not separated.

## 3.2 — Cleaning the corpus, and what the instruction was really worth

Three fixes from what 3.1 exposed, each measured before and after.

**Open-application pages are not vacancies** and are dropped when the corpus is
loaded (`joblens.corpus.is_vacancy`). Filtering happens on the way *out* of the
store, not on the way in: the store keeps what the boards published, and what
counts as searchable stays a decision we can change and re-measure without
fetching anything again.

**Duplicate detection was too trusting of labels.** `Vacancy.fingerprint` compares
title, company and place, and the real corpus beat it twice: one nursing job was
listed by indeed with no company and the city written "Utrecht" once and "UT" the
other time, and one job was advertised under two names of the same business
(BOSMAN and MediReva). Both pairs are byte-identical text.

So there is a second mark now, `content_fingerprint` — the simplified title plus a
hash of the text. Two refinements, both forced by real data rather than guessed:

- **Identical text alone is not enough.** Catawiki posted a Category Manager for
  Coins & Banknotes and one for E-Commerce with the same description pasted into
  both. Two real jobs, one description — so the title has to match as well, and
  those two survive.
- **Place is compared separately, not hashed in.** A test we already had says a
  Data Engineer in Amsterdam and one in Rotterdam are two jobs; the corpus says
  "UT" and "Utrecht" are one place. A set of strings cannot express "compatible",
  so `SeenJobs` holds the places seen per content and `same_place` decides: equal,
  or one missing, or an abbreviation of three characters or fewer that the other
  starts with. Amsterdam and Amersfoort stay apart.

202 vacancies become **198**: two open applications, two duplicates.

**What the cleanup bought** (hit@1, dev / holdout):

| Variant | before | after |
|---|---|---|
| local structured, no instruction | 40% / 50% | **67% / 62%** |
| gemini-2 raw | 60% / 38% | 73% / 38% |
| gemini-2 structured | 87% / 88% | 87% / 88% |
| local raw | 80% / 75% | 80% / 75% |

The variants that were already good do not move at all — they were never fooled by
the junk. The bad ones recover most of the gap. That is the clearest statement of
what those two documents were doing: not making search a bit worse everywhere, but
destroying the variants that had nothing else to go on.

**This overturns what 3.1 said about the query instruction.** That entry read the
40% → 67% gap as the instruction being worth far more on real data than on the
samples. It was not: **once the two junk pages are gone the instruction is worth
nothing on hit@1** (67% against 67% on dev, 62% against 62% on holdout) and only a
little on MRR (0.74 against 0.71). What the instruction had been doing was keeping
one specific junk document off the top spot, and with the junk gone there is
nothing left to correct. The lesson is not about instructions: **a broken corpus
makes every other measurement mean something other than what it appears to.**

**New defaults**: `gemini-embedding-2`, document style `structured`. `.env.example`
carries the local block, commented, one edit away — it costs about 13 points of
hit@1 and sends nothing anywhere.

## 3.3 — Chunking: it rescues raw text, and still does not beat the summary

The last question from the CV-matching brief: does one vector per vacancy lose
things? Answered as three more eval variants rather than as a rewrite of
`VacancyIndex`, because the expected answer was "no" and a negative result should
be cheap.

**What chunking is for, and what it is not for.** Two problems get confused here.
*Truncation* is the document being longer than the model's window, so the tail is
silently dropped — ruled out for this corpus in 2.4 (longest vacancy 11,188
characters, gemini-2 cuts at ~42,000). *Dilution* is one vector being the average
meaning of the whole text, so a query about one narrow part matches it weakly.
Dilution happens at every window size; a model with a bigger window would simply
average more text into the one vector. Only the second is a reason for us.

`chunk_text` splits on paragraph breaks, packs to ~900 characters and carries a
150-character tail into the next chunk. A vacancy then scores as its **best**
chunk (`rank_all_pooled`, a scatter-max over the chunk-query cosines), not its
average.

| Variant | dev hit@1 / MRR | holdout hit@1 / MRR |
|---|---|---|
| gemini-2 raw | 73% / 0.80 | 38% / 0.56 |
| **gemini-2 raw, chunked** | **87% / 0.92** | **75% / 0.84** |
| gemini-2 structured *(the default)* | 87% / 0.90 | 88% / 0.91 |
| gemini-2 structured+raw, chunked | 87% / 0.89 | 75% / 0.84 |
| local raw | 80% / 0.85 | 75% / 0.88 |
| local raw, chunked | 80% / 0.85 | 62% / 0.76 |

**Chunking repairs raw text almost exactly as predicted.** `gemini-2 raw` went
from the worst variant in 3.1 to 87% / 75%, a jump of 14 and 37 points. The
mechanism is the one 3.1 identified: the employer boilerplate ends up in chunks of
its own, and a chunk of Adyen's company story never wins a max-pool against a
chunk that actually describes the job. Chunking does not remove the boilerplate;
it *quarantines* it.

**And it still does not beat the summary.** Against the `structured` default,
chunked raw ties on dev (87% both, MRR 0.92 against 0.90) and loses holdout by one
query (75% against 88%). One holdout query is 12.5 points, so this is a tie we
cannot call — which is the point. Chunking buys nothing measurable over a document
that never had the dilution problem, because `structured` is already ten fields
and no prose.

**So cost decides.** Chunking `raw` is 1,628 vectors instead of 198, and
`structured_raw` is 1,737 — **8 to 9 times** the embeddings to store, refresh and
pay for, for no gain we can demonstrate. `structured` stays the default, one vector
per vacancy.

**What this does not settle.** The dilution argument was always weaker for a
vacancy (one advert, one job) than for a **CV** (five jobs, education and skills in
one vector). Nothing here measures that, and the 3.4 CV work is where the question
actually bites. The chunker and the pooled ranking stay, tested and unused, because
that is the side they were really built for.

**Method note.** Deciding this as eval variants cost an afternoon; the
`VacancyIndex` rewrite it replaced would have cost a day and would now be carrying
a feature the numbers say not to use.

## 3.4 — Reading a CV: what is removed, and what a run costs

The first half of CV matching. A CV goes in as a PDF, a .txt or a .md; what comes
out is a `CVProfile` — jobs with dates and skills, education, certificates,
languages — and, before that, a printed list of everything that was stripped out
of it. `scripts/read_cv.py` is deliberately its own script: you get to see what
would be sent before anything is.

**One extraction loop, two things to extract.** Vacancy extraction already had the
part worth keeping: schema in the prompt, parse, and on a validation failure show
the model its own answer plus the exact errors and let it try again. That moved to
`llm/structured.py` and now takes any pydantic model; `extraction/extract.py` is
the vacancy prompt and nothing else. The 11 existing extraction tests passed
unchanged, which is the only evidence worth having that a refactor did nothing.

**What a CV costs to read** (gemini-3.8-flash, the three sample CVs):

| CV | tokens in / out | cost | time |
|---|---|---|---|
| Lisa de Vries (PDF) | 2,378 / 813 | 0.48 cent | 2.9 s |
| Youssef Bakker | 2,418 / 864 | 0.51 cent | 2.9 s |
| Ingrid Solheim | 2,251 / 704 | 0.43 cent | 2.8 s |

So **half a cent and three seconds per CV**, once. That answers half of question 2
in the brief. The other half is an estimate until 3.6 measures it: a judge call
over one vacancy is ~2,700 tokens in and ~400 out, which is **0.35 cent**, so
judging the top 20 comes to about **$0.07 per run** and judging all 198 to
**$0.70**. Prices live in `llm/pricing.py`, keyed by exact model ID with the date
they were read; a model with no entry prints "unknown", never "free".

**The sample CVs found three bugs that invented-looking text would not have.**
They were written to look like real Dutch CVs, and that alone was enough:

- `06 - 3318 2245` — a phone number with spaces around the dash — was **not
  removed**. The vacancy pattern allowed one separator character between digits.
  Widened to three, which is still safe because both shapes have to start with a
  `0` or a country code, so "3200 - 3800" and "2016 - 2019" stay put.
- "augustus 2023\nAd-hoc analyses" was read as a **postcode** and deleted. So was
  "sinds 2019 en 2021". Four digits and two letters is a postcode; four digits and
  the word "en" is a sentence. The pattern now refuses a line break as the
  separator, refuses a hyphenated word after the letters, and refuses a short
  lowercase word — "1016 EN" is a real Amsterdam postcode and still goes.

The general point: redaction fails in two directions and only one of them is
visible. A missed phone number is a leak; a deleted job date is a worse match with
no explanation. Printing every removal is what makes the second kind findable, so
the removal list is a feature and not debug output.

**Where redaction stops working, kept visible.** `ingrid_solheim.md` is Norwegian:
her e-mail, phone and date of birth go, and "Storgata 71, 9008 Tromsø" stays,
because the patterns are Dutch. That is left in the repository rather than patched,
so the limit is something you can see rather than something you assume is handled.

**"Years of experience" is not one number, and saying so was the fix.** Youssef's
CV says "ruim acht jaar"; adding up his dates gives **13.7**. Both are right: he
lists a supermarket side job through three years of school, and a CV does not say
how many hours a week any job was. The method is now named for what it does —
calendar years covered by the jobs listed, overlaps merged so two concurrent jobs
are not counted twice — and the script prints "13.7 years covered by listed jobs".
Experience as a judgement belongs to the matching step, which can read the whole
CV; arithmetic that a model would have guessed at is done in code, where it is
tested.

**Question 1 from the brief, on invented CVs**, answered as far as it can be
answered yet: they keep the repo runnable, and they will flatter retrieval,
because whoever writes one has already read the vacancies. `data/samples/cvs/`
says so, and the rule for 3.5 is that eval numbers are reported per CV and never
pooled — the same discipline that would have caught the 2.2 mistake.

**A scanned CV is refused.** A PDF with no text layer extracts to an empty string,
and an empty CV would otherwise produce a confident, meaningless ranking. It raises
with an explanation instead. `scripts/make_sample_pdf.py` (a small PDF writer, no
dependency) produces both the committed sample PDF and the no-text fixture the test
uses.

**New dependency: pypdf** — MIT, pure Python, no transitive dependencies. Rejected:
pdfplumber (heavier, better at columns — add it the day a real CV needs it),
PyMuPDF (AGPL, wrong for a public repo), OCR (out of scope; the error says so).

**Not settled here.** Nothing in this milestone matches anything. Whether a CV
should reach the index as its whole text, as a structured profile, as one query per
job, or as an invented "ideal vacancy" is 3.5, and it is a question for the eval.

## 3.5 — How should a CV ask the question? The dull answer wins

A CV now searches the real corpus. The question this milestone had to settle was
what a CV *is* when it reaches the index, because a vacancy is one job and a
career is five, plus an education and a pile of skills. Five candidates, measured:

| cv style | what it sends |
|---|---|
| raw | the redacted CV text, as one query |
| profile | the extracted fields as a summary, in the same Dutch labels a vacancy is indexed with |
| roles | one query per job, plus one for education and skills; a vacancy scores as its best part |
| chunks | the CV cut into overlapping pieces by `chunk_text`, pooled the same way |
| wishlist | a model writes the advert this person would be hired for next, and that is the query (HyDE) |

Four labelled CVs, all invented, judged by Claude: a data analyst, a warehouse
voorman, a GGZ nurse, and an Arctic marine biologist who fits nothing. **Nothing
is averaged across them** — nDCG@10 per CV, and a variant wins by not failing
anyone:

| variant | lisa | sanne | youssef | worst |
|---|---|---|---|---|
| **gemini-2 raw CV** | 0.79 | 0.76 | 0.88 | **0.76** |
| gemini-2 chunks | 0.80 | 0.71 | 0.72 | 0.71 |
| gemini-2 profile | 0.67 | 0.83 | 0.91 | 0.67 |
| gemini-2 roles | 0.80 | 0.79 | 0.67 | 0.67 |
| gemini-2 wishlist | 0.65 | 0.79 | 0.94 | 0.65 |
| local profile | 0.83 | 0.83 | 0.52 | 0.52 |
| local raw CV | 0.87 | 0.60 | 0.48 | 0.48 |
| local roles | 0.54 | 0.50 | 0.46 | 0.46 |

**No variant wins on every CV, so the rule had to be written down before reading
the table: pick the best worst case.** A job seeker gets one list, and a
representation that is brilliant for two people and useless for the third is not
usable. On that rule `gemini-2 raw CV` wins, and it is also the only variant with
hit@1 of 100% on all three scored CVs. It is the cheapest (one embedding, no
extra model call) and the least clever.

**The dilution argument did not transfer.** Splitting a CV into its jobs was the
expected winner — 3.3 left the chunker "for the side it was really built for",
which was this one. It came fourth. The likely reason is that the premise was
half wrong: dilution needs a long document, and these CVs are 1,700-2,300
characters against a vacancy's 5,100, while the thing they are compared *to* is a
280-character structured summary. Two short texts do not dilute each other. The
lesson repeats 3.3's: a mechanism that is real in one place is not thereby real
in the next one, and the cheap way to find out is a variant in the eval rather
than a rewrite.

**The local model is not a 13-point drop here, it is a cliff.** On vacancy search
qwen3-embedding cost about 13 points of hit@1. On CV matching its worst case is
0.48 against 0.76 — it handles Lisa (0.87, the best score anyone got) and falls
apart on Youssef. Whatever "keep it local" costs, it is not one number, and it is
much larger for a CV than for a query.

**The control CV is the useful part.** Ingrid Solheim fits nothing, and every
variant still hands her a number one. Her top score against a CV that does fit:

| variant | ingrid (no fit) | lisa | sanne | youssef |
|---|---|---|---|---|
| gemini-2 raw CV | 0.646 | 0.773 | 0.762 | 0.766 |
| gemini-2 profile | 0.766 | 0.820 | 0.825 | 0.826 |
| gemini-2 wishlist | 0.729 | 0.838 | 0.842 | 0.833 |

**A refusal cannot be a cosine threshold.** The gap is 0.12 for `raw` and 0.05
for `profile`, on one control CV. Nothing that thin, measured once, is a
threshold — 3.7 has to get "nothing here fits you" from the judge reading the
vacancy, not from the number. Worth knowing before building it the cheap way.

**And the app does read the CV.** Top-10 overlap between two people's lists is 0%
for every pair of the three real CVs under every variant. The one bad number is
`raw`, where Ingrid shares **50%** of her top ten with Lisa: two English-language
technical CVs land in the same region of the space even when one of them is about
copepods. The winner is the weakest variant at keeping an unmatchable CV away
from someone else's list, which is a real mark against it and another reason the
refusal has to be an explanation rather than a score.

**What this is worth.** Four invented CVs, judged by the same model that does the
matching, against a corpus of 198. One CV is one row, and one row moving would
change the winner: the gap between `raw` (0.76) and `chunks` (0.71) is smaller
than the spread of a single CV. This is "the ordering is not obviously broken",
not "matching works" — the real test is Mahdi's CV, judged by Mahdi, and
`evals/cv-matches/README.md` says so in the file itself.

**Method note: the eval got 20x faster by accident.** The first labelling run took
over fifteen minutes and the fix was not the embeddings. Every variant built its
own `CachedEmbedder`, and building one reads the whole cache file — 171 MB of
JSON for gemini-embedding-2, twenty-four times. `EmbedderPool` keeps one per
model for the length of a run: 43 seconds. Nothing about the measurement changed.

## 3.6 — The judge: every quote checked before anyone reads it

A shortlisted vacancy now comes back with a verdict, the lines of the CV that
answer it, and what it asks for that the CV does not show. The part that makes
this different from asking a model to be careful is that **the prompt is not
trusted**: every quote is looked for in the text it claims to come from, and a
quote that is not there is deleted along with the claim it supported.

```
 3. [STRONG    90] Verpleegkundige FHIC — Fivoor
    Je werkt al op een gesloten opnameafdeling in Den Dolder en bent
    BIG-geregistreerd; wat ontbreekt is ervaring met het FHIC-gedachtegoed.
    why it fits:
      + Crisisinterventie en de-escalatie op een intensieve afdeling
        your CV: "Vast aanspreekpunt bij agressie-incidenten en getraind in de-escalatie."
    what you are missing:
      - Ervaring met specifiek FHIC- of HIC-gedachtegoed  (a plus)
        the vacancy: "Er is ruimte voor mensen die een bijdrage kunnen leveren vanwege…"
```

**Measured over four CVs and forty vacancies** (`scripts/eval_judge.py`):

| | |
|---|---|
| quotes checked | 244 |
| found in the source | **100%** |
| `strong` on a vacancy labelled "would not apply" | **0** |
| `weak` on a vacancy labelled "would apply" | 2 |
| cost | $0.145, or **0.36 cent per vacancy** |
| time | 35s for 40 judgements, six at a time |

The two mistakes are counted apart on purpose. A `strong` on something the label
says not to apply to spends an application; a `weak` on something worth applying
to costs a line in a list. Zero of the expensive kind and two of the cheap kind
is the right shape for a tool that is meant to disappoint you.

**The refusal question from 3.5 is answered, and not by the number I expected.**
Ingrid Solheim, the Arctic marine biologist, gets **0 strong, 0 possible, 10
weak, highest fit 5** against a corpus where cosine could only separate her by
0.12 and still handed her a confident number one. The judge reads the vacancy and
says no. That is the mechanism 3.7 should build the "nothing here fits you"
answer on — not a similarity threshold, which 3.5 measured and found too thin.

**The first faithfulness number was not 100%, and the bug was mine.** One or two
claims per vacancy were being dropped, which looked like the judge inventing
quotes. It was not: the sample CVs are markdown, the source line is
`**MBO Verpleegkunde niveau 4 — ROC Midden Nederland, Utrecht**`, and the model
quoted it without the asterisks — correctly. The check was comparing formatting
as if it were content. `_searchable` now removes emphasis characters and
normalises typographic quotes and dashes on both sides, and keeps every word.

The distinction that matters: **normalising formatting is safe, accepting a
paraphrase is not.** A character that carries no meaning cannot make two
different claims look like the same one; a loosened word match can. This is also
why a dropped quote is never repaired by asking again — a model that invented one
line will invent the next, and a shorter list of true claims is the honest output.

**A number and a band, forced to agree.** `fit` alone drifts (what is 72?), and
three bands cannot order twenty vacancies. The schema requires the number to sit
inside the band's range, so a "weak, 90" fails validation and the repair loop
from 1.4 asks again. The rubric lives in the prompt, the check lives in the
schema, and neither is a comment.

**Retrieval chooses who gets a call; it does not choose the order.** The list is
sorted by verdict, then fit, and only then by cosine. Lisa's top-8 by similarity
comes back as 0 strong, 2 possible, 6 weak: the vacancies were close, and close
is not the same as worth applying to.

**Where the judge and the labels disagree.** Lisa is the case: her labels call
four data-engineering roles "would apply" and the judge calls them `possible`,
because she is a BI analyst and they ask for platform engineering. That is a real
difference of opinion rather than an error, and it is the direction the brief
asks for. Sanne, whose CV matches her sector squarely, gets 4 `strong` on 4
"would apply".

**What is stored is the model's raw answer, not the checked one.** Re-running
`verify` over a saved answer costs nothing, so a change to the check can be
measured against judgements already paid for; only a new prompt version buys new
calls. That is how the markdown fix above was confirmed without spending
anything.

**Still open for 3.7**: the gap list across the whole run ("what keeps coming up
that you do not have"), and the refusal itself.

## 3.6.1 — The first real CV, and the three things four invented ones could not find

Everything up to here was measured on four CVs that we wrote. They were written
to look like real Dutch CVs and they earned their keep: in 3.4 they found two
redaction bugs on their own. Then the app was pointed at a real PDF for the first
time and broke in three places at once, all of them downstream of one property no
invented CV has — **it was made by a real PDF exporter.**

**The PDF has a text layer that does not say what the page shows.** Its font is a
subset, and its own `ToUnicode` map points at the private use area:

```
<0551> <0555> <E073>          the CMap the file itself carries
Founder & Full-Stack Developer Dec   Sep …
```

114 characters over 33 of 144 lines, including every year in the work history and
the `+` of the phone number. This is not a parsing failure that a better library
fixes: the file does not contain the characters it displays, and only the glyph
outlines know, so reading them back is OCR. `pdfplumber` would return exactly the
same thing, more slowly.

**1. The phone number was sent to the cloud.** `PHONE` requires a `+` or a
leading `0`; the `+` was ``, so `+31 6 18295250` arrived as `31 6 18295250`
and matched nothing. It went to Gemini in the extraction call, in all fifteen
judge calls, and — because the redacted text is also the query — to the embedding
API, where it is still a cache key in `data/cache/`. Two new rules, both in
`cv/clean.py`: an explicit calling code with no `+` in front of it, and any run of
eight or more digits. The calling code is an explicit short list rather than
`\d{1,3}` because a generic prefix reads `2021 2022 2023 2024` as a phone number,
and nine digits are required after it because eight would eat `32.000 - 38.000`.

**2. The model invented the dates, and nothing in the output admitted it.** Given
`Founder & Full-Stack Developer Dec   Sep`, it answered `2024-12 - 2025-09` with
no hedge. Two of the five periods it produced were wrong, and the report printed
"1.1 years covered by listed jobs" underneath them as a fact. That number is
computed in code precisely so it cannot be guessed — and it was, because the
arithmetic was honest about dates that were not.

Two fixes, in the order they act. An unreadable character is now replaced by `?`
rather than dropped, so the model is handed `Dec ???? ? Sep ????`: a hole it can
see instead of a gap it can fill. And `verify_dates` (`cv/extract.py`) applies
3.6's rule one step earlier — **a year in the profile must appear in the CV text
or the field becomes null** — using the same comparison as the quote check, now
in `cv/verify.py` so there is one of it and not two.

The marker turned out to do the work on its own: on the re-run the model returned
`null` for every date rather than a plausible one, and the report says "no dated
jobs". `verify_dates` caught nothing, which is what a backstop looks like when it
is working.

**3. The 96% faithfulness was the check being right, not the judge being wrong.**
Four of 105 quotes were dropped, the first time below 100%, and the obvious story
was that the glyph damage had made the judge hallucinate. Re-judging those two
vacancies says otherwise:

```
CV text (what was sent):   supabase postgresql, mongodb, sqlite
the judge's quote:         Supabase (PostgreSQL), MongoDB, SQLite
```

Three of the four were the model **silently repairing the broken glyphs** and
quoting the CV as a human would read it; the fourth quoted `Stack: Python 3.13, …`
as `Python, …`, dropping a version. All four are correctly dropped. The temptation
was to teach `_searchable` to ignore brackets, and that is exactly the loosening
the 3.6 rule forbids: normalising formatting is safe because a meaningless
character cannot make two claims look alike, but accepting the model's repair as
evidence means accepting its guess. So nothing was loosened. The run report now
prints the glyph warning next to the dropped-quote count, because they are one
event. With the holes marked, the same CV over 15 vacancies came back **106
quotes, 100% found** — one run, and the judge is not deterministic, so it is a
sign rather than a proof.

**Refuse or warn?** A scan is refused because there is nothing to read. This CV is
98.7% readable, and refusing it would make JobLens useless on the one CV it exists
for. So: refused above 10% unreadable characters, and below that read, marked and
warned about — loudly, with the damaged lines printed. The guarantee that matters
is not the threshold, it is that the unreadable *parts* are never guessed at, and
that holds for a CV with one broken glyph, which no threshold would ever catch.

**Also**: `--show-sent` now exists on `match_cv.py` as well as `read_cv.py`, and
pypdf's 64 log lines about broken CMap entries — which arrived before the first
word of output — are collected and reported as one line.

**What this says about invented test data.** 3.5 predicted that invented CVs would
flatter retrieval, because whoever writes one has already read the vacancies. The
real failure was duller and worse: an invented CV is *typed*, so it is clean
Unicode, and every bug above lives in the gap between "text" and "what a PDF
exporter produced". The same shape as 3.1, where ten fictional vacancies had no
shared employer boilerplate and no junk postings and therefore reversed every
conclusion. The fixture that now guards these three bugs (`tests/conftest.py`)
carries invented content and **real damage**, which is the part that had to be
copied.

Unchanged on purpose: the judge prompt, so `PROMPT_VERSION` stays 3.6 and the
eval still reads every judgement bought in 3.6. It reports the same numbers it
did then — 244 quotes, 100% found, 0 `strong` on a "would not apply" — for $0.

## 3.7 — What keeps coming up, what to say when nothing fits, and a run you can keep

The last two things the phase-2 brief asks for, and both of them are about the
run as a whole rather than one vacancy. Neither costs a model call: every number
below comes from a judgement already paid for in 3.6.

### What keeps coming up that you do not have

The brief calls this "the single most useful sentence this app can say to me".
3.6 left exactly the right raw material — each gap already carries a requirement,
a `required`/nice-to-have flag, and a quote from the vacancy **that was checked
against the vacancy text** before anyone saw it. 3.7 is the arithmetic over a
run of them (`src/joblens/cv/gaps.py`).

**The counting unit is a skill name the corpus already uses.** Grouping "Azure"
and "Microsoft Azure" is the one part of this that looks like it wants a model,
and phase 1 turns out to have bought the answer already: `VacancyDetails.skills`
is a canonical list of short skill names for all 279 vacancies. So the vocabulary
is the union of the shortlisted vacancies' skill lists, a gap joins a term's
bucket when it names that term as **whole tokens**, and then one merge pass folds
a bucket whose tokens are a superset of another *existing* bucket's into it. That
is the whole grouping: "Microsoft Azure" joins "Azure", "Azure DevOps" does not
join anything unless some gap raised "Azure" on its own, and "SQL" is never found
inside "MySQL".

The comparison is `cv/verify.py:searchable` — the same normalisation the quote
check uses, and for the same reason. 3.6's rule holds here too: **normalising
formatting is safe, accepting a paraphrase is not.**

**Weight is `fit / 100`, summed.** The brief asks for the requirements of "the
vacancies I came closest to", so a gap in a vacancy judged 80 has to count for
more than the same gap in one judged 20. There is no tuned constant and no band:
the number the weight is made of is printed on the same line.

**A vacancy votes once.** An advert that names Python under "wat je meebrengt"
and again under "wat je gaat doen" produces two gaps, and counting both would
print "in 2 of 10" about one advert. So each vacancy contributes its strongest
wording and nothing more. On Lisa's run this changed no number — all seven of her
"in 2 of 10" really were two adverts — which is what a guard looks like when the
data has not yet hit it.

**No model call, and here is the price of that decision.** One extra call per run
that groups the gap strings would cost **0.26 cent** (≈1,500 tokens in, 400 out
at gemini-3.8-flash), which is less than one judge call. It was not bought, and
the reason is not the money: a grouping is the one thing on this screen that
would have no source text behind it. Every other line can be traced to a quote;
a cluster label is a model's opinion about two of its own earlier opinions. What
is printed instead is the number that would justify buying it later —

| CV | gaps | grouped | field checks that fired |
|---|---|---|---|
| lisa_de_vries | 41 | **73%** | years (1 of 10 asks more than her dates cover) |
| ingrid_solheim | 47 | **55%** | language (3 of 10 require Dutch) |

— so a quarter to a half of the gaps name nothing the corpus calls a skill. They
are counted and listed as "could not be grouped" rather than dropped. Ingrid's
share is lower for a legible reason: a marine biologist's gaps are sentences
about a whole career being wrong, not missing tools.

**A second block needs no judge at all.** Education level, required languages and
`experience_years_min` were extracted into fields in phase 1, so they can be
compared with the CV in code: `meets_level`, the language list, and the years the
dates cover. A field comparison cannot hallucinate, and it is the only part of
this report that would survive the judge being switched off.

**And one honesty check falls out for free.** A gap naming a skill the CV *does*
list is left out of the headline — "what you do not have" cannot include
something you have, whatever the judge said — and reported separately, where it
is either the judge wanting more than a mention or the judge being wrong.

### The refusal: nothing here fits you

3.5 measured that this cannot be a cosine: 0.646 for the control against 0.766
for a CV that fits, twelve hundredths, on one control CV. 3.6 measured that the
judge can do it. 3.7 is the rule, and the rule needed **two bands, not one**,
which the data decided (`scripts/eval_judge.py`, all four CVs, 279 vacancies):

| cv | strong | possible | weak | outcome |
|---|---|---|---|---|
| ingrid_solheim | 0 | 0 | 10 | **nothing fits** |
| lisa_de_vries | 0 | 2 | 8 | nothing is a *clear* fit |
| sanne_vermeulen | 7 | 1 | 2 | ordinary answer |
| youssef_bakker | 3 | 3 | 4 | ordinary answer |

The obvious rule — "no strong means refuse" — would have refused Lisa, whose
labels name four vacancies she would apply to. So a refusal needs no strong
**and** no possible, and the middle band exists to say the weaker thing plainly
instead of overstating it. One rule over four CVs is a check that it is not
obviously broken, not a measurement, and `print_refusal` in the judge eval says
so on the same screen.

**What the refusal is not allowed to be.** Not silence, and not a shorter list.
The banner goes *above* the list — a refusal printed under ten formatted
vacancies has already been contradicted by the time you reach it — and the list,
the reason each one fails, and the gap summary all still print. The sentence also
claims only what was checked: *"the 10 closest of 279 raw vacancies were all
judged weak"*, not "nothing in the corpus fits you", which is a statement about
279 vacancies of which it read ten.

The real output, unedited:

```
==============================================================================
Nothing here fits you. The 10 closest of 279 raw vacancies were all judged weak
-- the best of them scored 15 out of 100.
They are still listed below, with the reason each one fails and the requirements
that keep coming up.
==============================================================================
```

### The run stamp becomes a file

3.6 printed a line of provenance and threw it away with the scrollback.
`src/joblens/cv/runs.py` stores it next to what the run concluded, under
`data/raw/cv-runs/`, and `scripts/compare_runs.py` reads two of them.

**Five things set the scale**, and if any differs the comparison is refused by
name: which CV was read, which embedder chose the shortlist, which model judged
it, under which prompt version, and which corpus (samples or raw). Subtracting
two numbers from different scales produces a number, and a number is what people
believe.

**The contents of the corpus are deliberately not on that list.** They change
every time `daily_update.sh` runs — 198 in 3.5, 279 today — so making that a
blocker would refuse nearly every real pair of runs, including the one question
worth asking a week later: is anything new better than last week's best? A
changed corpus is reported instead: the size, a digest of the sorted keys (279
and 279 can be two different 279s), what entered the shortlist, what left it, and
how the vacancies in both were judged this time.

**And the first thing it measured was the judge.** Two runs of the same control
CV six minutes apart, everything else identical: **8 of 10 judgements identical,
2 changed, both by a few points of `fit` and neither across a verdict boundary**
(weak 5 → weak 2, weak 5 → weak 4). So the non-determinism is real, it is small
at this temperature, and it lives inside the bands rather than across them --
which is the argument for reading the band and not the number, made with a number.

### The corpus grew from 198 to 279, and it moved every eval number but one

This was meant to be a footnote and is the most interesting result in the
milestone. The labels in `evals/cv-matches/` were pooled when the corpus was 198.
Re-running 3.5's eval on 279, with a new `labelled` column saying how much of
each top 10 was ever looked at:

| variant | lisa 3.5 → now | sanne 3.5 → now | youssef 3.5 → now |
|---|---|---|---|
| gemini-2 raw CV | 0.79 → **0.57** | 0.76 → **0.70** | 0.88 → **0.88** |
| gemini-2 profile | 0.67 → 0.54 | 0.83 → 0.46 | 0.91 → 0.91 |
| gemini-2 roles | 0.80 → 0.58 | 0.79 → 0.55 | 0.67 → 0.67 |
| gemini-2 chunks | 0.80 → 0.57 | 0.71 → 0.42 | 0.72 → 0.72 |
| gemini-2 wishlist | 0.65 → 0.53 | 0.79 → 0.70 | 0.94 → 0.94 |
| **labelled share** | 60-80% | 40-70% | **100%** |

**Youssef's five numbers are identical to five decimal places of the old ones,
and his coverage is the only one at 100%.** That is as clean a demonstration as
this project is going to get that the drop is the *labels* and not the retrieval:
an unlabelled vacancy scores gain 0 exactly like one judged "would not apply", so
a variant is punished for finding something nobody read.

So: **yes, a re-pool is needed, for Lisa and Sanne.** Not done in 3.7, because
re-labelling is an opinion and the file has to say whose. What 3.7 does is make
the shortfall impossible to miss — the column prints on every run, with a line
under the table saying the scores are a floor.

The 3.5 conclusion survives anyway, on the rule that was written down before the
table was read. Best worst case across CVs: `raw` 0.57, `roles` 0.55, `wishlist`
0.53, `chunks` 0.42, `profile` 0.46. **`raw` still wins**, by less, on scores
that are all floors. The control gap is unchanged at 0.11 (0.672 against 0.784),
so the refusal still cannot be a threshold.

**Also fixed here**: `index_vacancies.py` defaulted to `--style structured_raw`
while 3.1 settled on `structured`. Harmless but it warmed a cache nothing reads.

### The four questions at the end of the brief, with today's numbers

**1. Is a made-up CV good enough, or does the gap distort what we measure?**
Good enough to keep the repo runnable, and not good enough to measure on. 3.4
said invented CVs would flatter retrieval because whoever writes one has read the
vacancies; 3.6.1 found the real failure was duller and worse — an invented CV is
*typed*, so it is clean Unicode, and the three bugs a real PDF found all live in
the gap between "text" and "what a PDF exporter produced". 3.7 adds a third
shape: the four invented CVs are also invented *against a snapshot*, and two of
the four now have a top 10 that is 40-80% unlabelled. An invented CV ages. The
answer the repo ships with is the split it already has: sample CVs so the thing
runs end to end for anyone, numbers reported per CV and never pooled, and the
labels file saying in its own text that this measures Claude against Claude.

**2. Explain every vacancy or only the best few, and what does each cost?**
Only the shortlist, and the cost is now measured rather than estimated.
Half a cent and ~3s to read a CV (3.4, once, then cached). **0.36 cent per
vacancy judged** (3.6, 40 judgements) — measured again in 3.7's own runs at
$0.033 for 10 and $0.040 for 10. So **about 4 cents and 25-30 seconds per run at
`--top 10`**, against **about $1.00** to judge all 279 — which is 13 minutes of
model time, or roughly four minutes of waiting at six calls at a time. The
aggregate gap and the refusal add **zero** on top: both are arithmetic over
judgements already bought. What the cheap choice loses is real and 3.5 named it —
retrieval decides who gets a call, and its hit@1 is not 100%, so a vacancy
ranked 11th is never read. That is the trade being made: a 25x cost for the
vacancies ranked 11-279, most of which the judge would call weak.

**3. How do I compare two scores from different CVs, or a changed corpus?**
You cannot, and the app now stops pretending in code rather than in a footnote.
A cosine is not comparable between CVs (3.5 said so; the control scores 0.646 and
a good match 0.766 — the *same* number can be either). A `fit` is not comparable
across judge models or prompt versions. `compare_runs.py` refuses on all five of
those and names which one moved. The corpus is the one thing it will compare
across, because it changes by itself and refusing would make the tool useless;
what it does instead is report what entered and left.

**4. What should it do when the CV fits nothing?** Say so, in the first line,
and then show the closest few with why each fails and what keeps coming up. Built
on the verdicts, because 3.5 measured that the score cannot tell (0.12) and 3.6
measured that the judge can (0 strong, 0 possible, 10 weak, top fit 5 — top fit
15 on today's corpus). Never silence, never a padded list.

### "How I will know it worked" — the brief's own list, honestly

**Done.**

- *"Point it at my own CV and get back ranked vacancies, each with the evidence
  behind it and an honest list of gaps."* Yes, and the evidence is checked rather
  than promised: 245 quotes over four CVs in 3.7's run, **100% found in the
  source**, with the 3.6.1 machinery that keeps a damaged PDF from being guessed
  at.
- *"I can see roughly what a run costs, in money and in time."* Printed in the
  footer of every run and stored in the artifact. See question 2 above.
- *"What keeps coming up that I do not have."* This milestone.

**Partly done.**

- *"The made-up CV produces visibly different results — different jobs, different
  gaps."* The jobs: measured, top-10 overlap is **0% between every pair of the
  three CVs that fit something**. The one bad number is still the control against
  Lisa at 40% (two English-language technical CVs land in the same region), which
  is a mark against `raw` and was one of the reasons the refusal is an
  explanation and not a score. The gaps: visibly different in 3.7's two real runs
  (Lisa gets dbt/Snowflake/ML, Ingrid gets Dutch and Python), but that is two
  runs read by eye, not a number.
- *"Some measurement of quality beyond my opinion."* It exists — four labelled
  CVs, nDCG/hit@1/MRR per CV, faithfulness, verdict-against-label with the
  expensive and cheap mistakes counted apart — and the labels file says in its own
  text who judged it. What is now true and was not in 3.5: **the labels no longer
  cover the corpus.** 40-80% of two CVs' top tens are unlabelled and the scores
  are floors. Measurable, measured, and printed; not yet repaired.

**Not done.**

- *"I can point it at my own CV"* — done as software, but the deeper item behind
  it is not: **Mahdi's CV has never been labelled**, so every quality number in
  phase 2 is still Claude ranking Claude's judgement of Claude-written CVs.
  `evals/cv-matches/README.md` has said this since 3.5 and it is still the single
  largest hole in the evidence. The real test is one real CV, labelled by the
  person it belongs to, and it is one file in a gitignored directory away.

## 4.1 — The 267 rejections nobody could see

Phase 3 is a developer-facing UI and the data work it needs, and this is the data
work. A run stored the ~12 vacancies it judged. The other 267 were rejected by
retrieval with no score, no position and no record that they had ever been
considered — and a rejection you cannot see is one you cannot argue with.

**It was already being computed and then thrown away.** `rank_pooled` scores
every vacancy and returns `hits[:top_k]`; `search_with_cv` asked for ten.
`rank_with_cv` asks for all of them, judging reads the head of that list, and the
rest is stored with its score, its position and which part of the CV it was
compared against. **Zero extra API calls**: the vectors are in memory, the cosines
were already calculated, and the run got 46 KB bigger.

### What it found on the first real run

Mahdi labelled his own CV in 3.7.2 — nine vacancies he would apply to, pooled
from every variant in the eval. Here is where this configuration's ranking puts
them (`--top 12`, the run he labelled against):

| his label: would apply | rank | score | read by the judge? |
|---|---|---|---|
| Full-stack Developer | 3 | 0.709 | yes |
| Python Software Engineer – AI team | 6 | 0.698 | yes |
| Python Software Engineer – Product team | 10 | 0.682 | yes |
| Full Stack Developer @ Bos Logistics | 11 | 0.681 | yes |
| **Student AI Developer** | **13** | **0.679** | **no** |
| Software Engineer | 17 | 0.677 | no |
| Frontend Engineer | 35 | 0.658 | no |
| Front-end Developer | 50 | 0.646 | no |
| Full-Stack Java Developer | 62 | 0.640 | no |

**Five of the nine never reached the judge.** The judge disagreed with Mahdi five
times out of ten in 3.7.2, which is the number that started phase 3; retrieval
disagreed with him five times as well, silently, and those are a different five.

**The first one it cut costs a thousandth of a point.** #12 scored 0.680 and #13
scored 0.679 — and #13 is a job he would apply to. That gap is now printed at the
end of every run, because it is the honest size of the decision `--top 12` makes:

```
the shortlist was cut between #12 Medior /Senior Java Software Engi… (0.680) and
#13 Student AI Developer (studying in… (0.679) — a gap of 0.001.
```

The whole ranking is that flat at the top: #1 is 0.711, #12 is 0.680, #50 is
0.646, #279 is 0.529. Thirty-one thousandths separate the best vacancy in the
corpus from the twelfth, and 3.5 already measured that a cosine cannot tell a
good CV from the control at twelve hundredths. Reading a *band* of the ranking
and not a number applies here too.

**What depth would cost.** At 0.51 cent per judgement on this CV (it is longer
than the invented ones: 4,455 tokens in per call against ~2,700 in 3.6):

| `--top` | applications reached | cost | model time |
|---|---|---|---|
| **12** (today) | 4 of 9 | $0.06 | 38 s |
| 20 | 6 of 9 | $0.10 | 63 s |
| 35 | 7 of 9 | $0.18 | 111 s |
| 50 | 8 of 9 | $0.25 | 158 s |
| **62** | **9 of 9** | $0.32 | 196 s |

Six workers, so 196 seconds of model time is about half a minute of waiting. This
is not yet an argument for `--top 62`: 13 vacancies he labelled "would not apply"
also rank above #62, so a deeper shortlist buys five applications and thirteen
more things to read past. What it is, is the first time that trade has had
numbers on both sides. Whether the judge sorts those 18 correctly is a judge
question, and this milestone does not touch the judge.

**The labels are doing their job.** Those five were pooled from other variants'
top tens, so they are not newly discovered jobs — they are jobs *this* variant
ranks below its cut and another variant ranked above. That is exactly the case
`label_cv_matches.py` pools for, and it would have been invisible in a run that
stores only what it judged.

### The rejections that never even got a score

A ranking explains why #83 was not read. It cannot say anything about a vacancy
that was not in the ranking at all, and three filters run before it: an
open-application page is not a job (3.1), the same job from two boards is loaded
once, and a vacancy with no extracted fields cannot be embedded like the rest.
`Corpus.funnel` counts all three where they happen and every run stores it:

```
279 of 283 stored vacancies could be ranked; 4 never had a chance:
2 open applications, 2 duplicates
```

Four out of 283 today, and the point is not the four. It is that the number is
printed rather than assumed: the day an extraction run half-fails, `never
extracted: 60` appears on the line above the results instead of sixty vacancies
quietly not existing.

### Two decisions worth naming

**The ranking is stored self-contained**, with each vacancy's title and company
copied in — 279 rows, 46 KB. The alternative was keys and scores alone, resolving
the rest from the corpus when viewing, which is smaller and wrong: `daily_update.sh`
rewrites the corpus and boards take adverts down, so a run has to stay readable
when the vacancy it names is gone. `JudgedRow` already copied titles for that
reason; `RankedRow` does the same.

**A vacancy whose judge call failed is still marked `judged`.** It was sent and
it cost money, so filing it with the 267 that were never read would hide a
failure among the things it is least like. The `failures` list says which ones
came back with nothing.

Old runs still open: `ranking` and `funnel` default to empty, so the five run
files from 3.7 — the only "before" this milestone has — load unchanged.

### And one thing that was not a milestone at all

`evals/cv-matches/mohammed.json` was untracked but **not ignored**, so a single
`git add -A` would have committed which real jobs a real person would apply to,
to a public repository. `.gitignore` now allowlists the four invented CVs' label
files and ignores the rest, so the next real CV is ignored by default. Being
wrong in that direction costs a `git add -f`; being wrong in the other direction
cannot be undone.

## 4.2 — One way in and out, before there is anything to put in a database

Phase 3's second step, and the least visible: nothing new happens, but every read
and write of a run, a label or a preference now goes through `joblens.storage`
instead of through a `Path` that each script built for itself. Five scripts had
their own copy of `ROOT / "evals" / "cv-matches"`.

**A run is an id, not a path.** `compare_runs.py 2026-09-22_1442_mahdi
2026-09-22_1555_mahdi` rather than two filenames. That is the single change that
makes the swap cheap later: an id is what a document store takes, and nothing
above the seam knows whether it names a file, a row or a Firestore document.
`FileStore._run_path` refuses anything with a slash or a leading dot in it,
because the web server in 4.3 will eventually be handed `../../.env` by
somebody.

**No database, and the reason is not "too early".** The queries this app actually
asks are "show me that run" and "show me every label" — no joins, no aggregate,
no concurrent writers. A file per run is diffable and greppable, and a run is a
document rather than a row. What a database would buy is listing without opening
every file, and that is why `runs()` returns a `RunSummary` and not a
`RunRecord`: the interface is already shaped for the version that answers it from
an index. A store earns its place at a second user or a server that cannot share
a filesystem, and by then 4.5 will have written a preferences schema from real
reasons instead of a guess.

**No `owner` parameter either.** With one person it would be the same string at
every call site, and an unused field is a lie about what the code does. A second
person is a directory level inside `FileStore`, or a collection in a Firestore
store: the implementation changes and no caller does, which is what a seam is
for.

**Labels are two things and now live in two places.** The four invented CVs'
labels are the repo's public evidence — clone it, run the eval, get the same
numbers. A real person's labels say which real jobs that person would apply to,
which is personal data. Reading merges both directories; writing follows the file
that already exists, so **a CV nobody has labelled is private by default**.
`mohammed.json` moved to `data/raw/cv-labels/` in this milestone, which is where
the 4.1 `.gitignore` guard was only a second line of defence.

**One bug fixed on the way.** The old `save_run` named a file after the minute it
ran, so two runs in the same minute silently became one — and there are already
two files a minute apart in `data/raw/cv-runs/`. A viewer that can start a run
makes the collision likely rather than theoretical, so the store appends `-2`
instead. Losing the earlier run means losing the "before" of whatever the second
one was testing.

Eleven new tests, and the four that matter are: an id is not a path, a missing
run raises rather than returning an empty record, a new CV's labels are private,
and a committed CV's labels stay committed.

## 4.3 — The viewer, and what 267 rejections look like when you can see them

The brief asked for a read-only page over a stored run: recommended,
judged-and-rejected, never-shortlisted, with the vacancy text and the extracted
fields one click away. `uv run python scripts/serve.py` and
<http://127.0.0.1:8000>.

**Built by hand, and here is exactly what that cost.** CLAUDE.md says no
framework until the thing has been built once. What FastAPI would have given us
is routing, JSON serialisation and static files; all three are in
`web/server.py` and together they are about a hundred lines. The page is
`web/index.html` plus 260 lines of plain DOM JavaScript and a stylesheet — no
build step, no node, no dependency added to `pyproject.toml`. What we do not have
is request validation, an OpenAPI schema and async. None of those is needed by a
page one person opens on their own laptop, and all of them will be the argument
for a framework the day this is hosted.

**The split that matters is in the API, not in the page.** `web/api.py` returns
three lists, because there are three genuinely different things:

| bucket | what it is | what it carries |
|---|---|---|
| recommended | strong or possible | a verdict, a fit, evidence, gaps |
| judged and rejected | a model read it and said no | the same, plus the reason it failed |
| never shortlisted | nobody read it | a rank, a score, and the part of the CV that matched it |

The difference between the second and the third is a fact about the run, so the
page is not allowed to decide it. And it is the difference the whole milestone is
for: a rejection with a reason can be argued with, and until 4.1 the third kind
had no reason, no score and no record at all.

**On the real run, the three buckets are 2, 10 and 267.** Opening
"never shortlisted" puts **Student AI Developer at #13, 0.679** against #12's
0.680 at the top of the list — a job Mahdi labelled "would apply", one
thousandth of a point below the cut, and the first thing the page shows him about
what he has been missing.

**One sentence has one author.** The refusal banner ("nothing here fits you") is
rendered from `cv/outcome.py`'s own `headline()` and `advice()`, sent over the
wire as strings. Rebuilding that logic in JavaScript would have created a second
opinion that drifts from the first, and 3.7 was careful about what that sentence
is allowed to claim.

**Three things the server refuses**, all tested against a real socket rather than
a mocked handler, because these are exactly the paths a unit test would stub out:
`/api/runs/..%2F..%2Fsecret` (a run id is a file stem, enforced in the store
since 4.2), `/../.env` and `/app.js/../../pyproject.toml` (static serving is
`web/` only, and only five extensions), and a missing key or a non-numeric limit,
which are 400s rather than tracebacks.

**What it deliberately cannot do.** It has no login, because with one user a fake
one is worse than an honest none. It binds to 127.0.0.1 and nothing else — it
serves a real CV and real vacancies. And it starts nothing: matching is still
`match_cv.py`, scraping is still the schedule. A page that could spend money by
being refreshed is not a read-only viewer.

**Every piece of data goes onto the page with `textContent`.** A vacancy is text
somebody else wrote, and a page that pastes it in as HTML is one advert away from
being a different page than the one you read.

## 4.4 — A label that says why

The reason phase 3 exists. Mahdi labelled his own CV in 3.7.2 and the judge
disagreed with him five times out of ten — three "weak" verdicts on jobs he would
apply to, rejected on years of experience or hbo/wo education, and one "strong"
at 84 that he would not apply to. Nobody could tell whether the judge or the
label was wrong, **because a label was a key in one of three lists.**

**The y/m/n prompt is gone.** It is the thing that produced those labels, and it
had two faults that are not fixable by asking the same question more politely:
it asked about a vacancy with 1,500 characters of it on screen, and it recorded
an answer with no sentence attached. Marking now happens in the viewer, where the
advert, the extracted fields, the judge's verdict and the retrieval rank are all
on the same screen as the buttons. `label_cv_matches.py` keeps the one thing the
viewer cannot do — pooling candidates across every variant in the eval config —
and points at the viewer for the rest.

**A reason is required, in the model and not only in the page.**
`CVLabels.record` raises without one, `POST /api/runs/{id}/labels` is a 400, and
the button says so. A call with no reason is what we already have 24 of.

**What is stored with it.** The vacancy key, the call, the reason exactly as
typed, when, which run was on screen, and — the part that makes the eval possible
— **what the judge had said about it at that moment**: verdict, fit, and the rank
retrieval gave it. A reason read a month later against a verdict that has since
moved is a different sentence.

| stored | why |
|---|---|
| `reason` | verbatim; nothing summarises, groups or generalises it |
| `verdict`, `fit` | so a disagreement can be printed as two lines side by side |
| `rank` | a vacancy nobody read has a rank and no verdict at all |
| `run` | the numbers can be found again |

**Nothing infers a rule.** The brief was explicit and it is worth writing down
why it is right: "three weak verdicts I disagreed with were all about years of
experience" is a pattern in three answers, and turning it into "Mahdi applies
regardless of stated years" is a rule he never stated, which would then quietly
decide future rankings. 4.5 will ask him to state it. This milestone only makes
sure the sentences exist to ask about.

**The three lists still work.** `relevant`, `maybe` and `judged` are kept in step
by `record()`, so every eval, every metric and the four committed label files
carry on unchanged — and a file written before 4.4 loads with an empty
`decisions` list rather than failing.

**Changing your mind is kept.** Decisions are append-only and the latest one
counts. A person labelling the same vacancy twice is data about the labels, not a
mistake to be overwritten.

**`eval_judge.py` prints the disagreements with the reasons.** That is the
"before" and "after" of this milestone in one screen. Run today, `--cv mohammed
--top 12`, on the stored judgements:

```
===== where the judge and the person disagree =====

  mohammed: judge strong 88, you would not apply
    All-round Frontend Developer
    your reason: none recorded (labelled before 4.4)

  mohammed: judge weak 32, you would apply
    Full Stack Developer / Engineer bij Bos Logistics
    your reason: none recorded (labelled before 4.4)

  mohammed: judge weak 28, you would apply
    Full-stack Developer
    your reason: none recorded (labelled before 4.4)

  mohammed: judge weak 20, you would apply
    Python Software Engineer -  AI team
    your reason: none recorded (labelled before 4.4)
```

**Four, where 3.7.2 counted five**, and the difference is worth naming rather
than rounding away: one of the five was judged `weak 24` then and `possible` now.
3.7 measured this exact effect — two runs of one CV six minutes apart moved two
of ten judgements by a few points — and here it moved one of them across a band.
So "five disagreements" was never a constant; it is a number with a run attached,
which is the argument for reading the band rather than the fit, made again.

Every mark made in the viewer from now on removes one of those "none recorded"
lines. That is the only progress measure this milestone has, and it is honest:
the reasons do not exist yet.

**Where the numbers stand right now** (`/api/runs/.../labels` on the real run):
`apply 9, maybe 1, judged 24, with_a_reason 0`. Twenty-four calls, no sentences.
That is the number 4.5 is waiting on — the brief asks for twenty real reasons
before a preferences schema is written, and guessing at the form instead is the
one thing it said not to do.

**Two bugs the tests found**, both of the kind that only appear when a seam is
actually used by something new:

- `FileStore.save_labels` serialised with `model_dump()` rather than
  `model_dump(mode="json")`, which worked perfectly until a label carried a
  timestamp. A store that can only write the models it was written for is not a
  seam.
- The router returned 405 for `POST /api/runs/{id}/labels`, because it stopped at
  the first route whose *pattern* matched and that route was the GET. One path
  answering two methods is the first thing a write endpoint needs.

## 5.1 — One gate for every request, and a memory of who said no

Phase 5 widens where vacancies come from (the plan and the measurements behind
it are in `docs/vacancy-sources-phase-5.md`). Every later step adds requests to
sites we have never asked before, so the first step is the thing they all go
through.

**Before this, politeness was a `time.sleep(1.5)` at the bottom of the fetch
loop.** It paused after every search, whoever it had gone to: between Adyen's
Greenhouse board and Channable's Recruitee board, which share nothing. And it
forgot everything at exit, so a site that throttled us last night got the full
run again tonight.

**What `sources/polite.py` does instead, per site:**

| rule | how |
|---|---|
| pace | `delay_seconds` + up to `jitter_seconds` between two requests to one site; different sites do not wait for each other |
| budget | `max_requests_per_site` per run; a runaway loop stops at our number |
| recognise a refusal | 429, 403, and the refusals that arrive as a normal page or a redirect |
| remember it | written to `data/raw/fetch-state.json` at once; not asked again for 12 h, doubling per refusal in a row, up to a week |
| forgive | a site that answers a whole run without refusing loses its entry |

**A site is the platform, not the host name.** `channable.recruitee.com` and
`nmbrs.recruitee.com` are two boards on one platform's servers. Fifty boards
paced as fifty hosts would be fifty times what one platform sees from one
address. `site_of` keeps the last two labels of the name, which is right for
every .nl, .com and .io here.

**The gate is an httpx transport.** A transport is what sits under the client and
actually sends the request. Putting the gate there means Recruitee, Greenhouse,
jobdataapi and LinkedIn's descriptions call `client.get` exactly as before, and
a new adapter cannot forget to be polite: there is no other way out. The
alternative, a wrapper client with its own `get()`, would have changed every
adapter and left the door open for the next one. JobSpy is the one exception:
it sends its own requests, so the fetch script asks the gate once per Indeed or
LinkedIn *search*, the only part of that traffic we can see.

**robots.txt is not in this milestone, and the reason is measured.**
The plan had it here. Then the robots.txt files of the hosts we already use:

| host | what it says to us |
|---|---|
| `jobdataapi.com` | `Disallow: /api/`, while documenting that API as a free product |
| `api.smartrecruiters.com` | `Disallow: /`, while documenting its Posting API as public |
| `boards-api.greenhouse.io`, `*.recruitee.com` | the API paths are allowed |
| `www.linkedin.com` | `Disallow: /` for everyone; `/jobs-guest/` is disallowed even for Googlebot |

robots.txt is written for crawlers and search indexes, and a documented API is
neither: its documentation and rate limits are the permission. Enforcing
robots.txt on every request would switch off two documented APIs, one of which we
already use. So it arrives in 5.4 with the first source that actually crawls web
pages, where it is the permission. LinkedIn's line settles what 2.4 left as a
choice: it stays off.

**Refusal pages were measured before they were written down.** Two markers, both
taken from real responses on 2026-09-22, no guesses:

- `_cf_chl_opt`: Cloudflare's challenge page (werkzoeken.nl, ictergezocht.nl).
  Not `challenge-platform`: Cloudflare loads a script by that name on the
  *normal* pages of sites it guards, and there is a test that says so.
- the "DPG Media Privacy Gate" consent wall (nationalevacaturebank.nl).

**The first real check found a hole the tests could not.** Pointed at a Nationale
Vacaturebank vacancy, the gate reported an ordinary `302`. curl had shown the
consent wall because `-L` follows redirects; httpx does not. The wall is a
redirect to `myprivacy.dpgmedia.nl/consent`. Following it would have blamed
`dpgmedia.nl` and recorded Nationale Vacaturebank as a site that answered
normally. So a redirect to a known wall is now a refusal on the redirect itself,
held against the site we asked. The same check again, same state file:

```
cooling_down  werkzoeken.nl: refused us (HTTP 403, a Cloudflare challenge page); not asking again until 2026-09-23 08:26 UTC
blocked       nationalevacaturebank.nl: a consent wall (DPG Media Privacy Gate); not asking again until 2026-09-23 08:27 UTC
cooling_down  werkzoeken.nl: refused us (HTTP 403, a Cloudflare challenge page); not asking again until 2026-09-23 08:26 UTC
requests sent: {'nationalevacaturebank.nl': 1}
```

Three requests asked for, one sent: werkzoeken.nl was remembered from the run
before, and not asked at all.

**The real sources, through the gate** (fresh store, 2026-09-22):

| source | boards | requests | stored |
|---|---|---|---|
| recruitee | 3 | `recruitee.com 3` | 30 |
| greenhouse | 3 | `greenhouse.io 3` | 110 |

The run report now carries `requests` per site, and `data_status.py` prints the
sites currently refusing us, with the reason and until when.

**Three smaller things the gate made visible:**

- `get_json` read `Retry-After` with `float()`. The header may also be a date,
  which would have crashed the run. Both forms are read now, in one place.
- A refusal ends every later search of that source for the same reason, so the
  report groups identical breakages: "indeed (12 searches): cooling_down" is one
  line in the cron mail, not twelve.
- A shorter `Retry-After` does not shorten our schedule. jobdataapi asked for
  2710 s in 2.3; a nightly run simply waits for the next night.

**What this is not.** No proxies, no rotating addresses, no browser disguise, no
retries. JobSpy's README calls proxies "a must" for LinkedIn. That is exactly the
advice this module exists not to follow. When a site says no, the source stops,
the report says so, and a person decides. Deleting a site's entry in
`fetch-state.json` is that decision.
