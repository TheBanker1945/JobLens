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
