# CV matching on the real corpus

I want JobLens to do the thing it was built for: I give it my CV, and it tells me
which of the real vacancies it has collected are worth my time, why each one fits,
and what I am missing. Everything up to now (extraction, embeddings, search,
scraping) was plumbing for this.

There is a step before that, and it is not optional: **we cannot tell whether
matching is any good if we cannot yet measure search on real vacancies.** All our
retrieval numbers come from ten vacancies I made up. So this milestone is two
phases: first make search measurable on real data, then build matching on top of
what that measurement tells us.

## How I want you to work

Explore the repo before planning. Nothing here should be built as a parallel
system: `VacancyIndex` already says in its own docstring that it exists for "a
search script today, CV matching and an MCP tool later". Use it, extend it, or tell
me why it does not fit.

Come back with a plan before writing code, per phase, and wait for my go-ahead.
Small steps, one at a time. If something in this brief is wrong or there is a
better approach, say so before building.

## What already exists (verified 2026-09-21)

- **202 real vacancies**, all extracted and all embedded: greenhouse 59, indeed 21,
  jobdataapi 93, recruitee 29. `scripts/data_status.py` reports freshness.
- `VacancyIndex` (`embeddings/index.py`): builds documents, embeds once, ranks by
  cosine, returns `Match(vacancy, score, document)` — the document is kept so a hit
  can be explained. `oversized()` warns when a document exceeds ~20,000 characters,
  because the embedder silently truncates past ~22,000.
- Document styles (`embeddings/documents.py`): `raw`, `structured`, `title_only`,
  `structured_raw`. The index and search script default to `structured_raw`, but
  the 2.2 eval found plain `raw` best. That was on my ten fictional vacancies and
  has never been checked on real text.
- Retrieval eval (`evals/retrieval.py`, `scripts/eval_retrieval.py`): hit@1,
  recall@3, MRR, dev/holdout split, per-variant config in `evals/retrieval.toml`.
  It is hard-wired to `data/samples/` and its labelled queries.
- Extraction results are stored per source and reused (`extraction/store.py`), so
  re-running costs nothing.
- Embeddings are cached per (model, text) in `data/cache/`.
- Local embedder: `qwen3-embedding:0.6b` on Ollama, 1,024 numbers per vector.

## Phase 1 — Make search measurable on real vacancies

**Goal:** the same eval we run on the fictional set, but over the 202 real ones, so
every later decision about matching is based on real numbers.

What I expect it to need:

- A corpus switch in the eval (samples or raw), reusing what is there rather than a
  second eval harness.
- **Labelled queries for the real corpus.** This is the part only I can do. Draft
  about 15 queries a Dutch job seeker would actually type, run them, show me the
  top results per query, and let me mark which are relevant. Keep my decisions in a
  guidelines file, as `data/samples/expected/README.md` already does. Split them
  into dev and holdout, the same discipline as milestone 1.6.
- Cloud embeddings in the comparison. **CVs may now go to cloud models** (CLAUDE.md,
  2026-09-21), which removes the reason the corpus had to stay local. In the 2.2
  eval `gemini-embedding-2` was perfect against 0.84 MRR local, on fictional data.
  Re-measure on real text before we switch anything.
- Settle `raw` versus `structured_raw` on real vacancies, and find out whether long
  real vacancies need **chunking** (a vacancy per vector today; some are 11,000
  characters).

**Done when:** `uv run python scripts/eval_retrieval.py --corpus raw` prints hit@1,
recall@3 and MRR per variant over real vacancies; the labelled queries and my
decisions are committed; and the learning log says which embedder and which
document style won, with the numbers.

## Phase 2 — CV matching with citations

**Goal:** `uv run python scripts/match_cv.py data/raw/cv/mahdi.pdf` gives a ranked
list of real vacancies, each with a score, the evidence from my CV that caused the
match, and an honest account of what I am missing.

What matters to me:

- **Citations, not vibes.** "Matches because of *this* line in your CV" and
  "requires X, your CV does not mention it". A number alone is worthless; I want to
  see what the machine used to decide.
- **The gap list is as valuable as the match.** If everything I look at needs Azure
  and I have none, that is the most useful thing the app can tell me.
- Reading a real CV: mine is a PDF. Read it locally; a CV never goes into git
  (`data/raw/` is gitignored, and `data/raw/cv/` should stay local even by accident).
- Chunking is probably where the quality is: a CV is not one idea, and neither is a
  vacancy. Whether we match whole CV to whole vacancy, or chunk against chunk, is a
  question for the eval, not for taste.
- The explanation is LLM work and belongs on the model we chose for extraction. It
  must quote the CV and the vacancy, and never invent experience I do not have —
  the same rule extraction already follows.
- Contact details in a CV (phone, email, address) are not needed for matching.
  Strip them before anything leaves the machine, the way `sources/clean.py` already
  does for vacancies. Cloud is allowed now, but "allowed" is not "send everything".

**Done when:** I can point the script at my CV and get ranked vacancies with
citations and gaps; a second CV (a made-up one, committed, so the repo stays
runnable for others) gives visibly different results; and there is some measurement
of match quality beyond my opinion — at minimum a small labelled set of
CV-to-vacancy pairs I judge once.

## Constraints

- Real vacancies and real CVs stay in `data/raw/`, never committed. Anything
  committed as an example is fictional.
- Cloud models are allowed for CV work when they measurably help. Keep it
  configurable and documented (own settings prefix, default written down), prefer
  providers whose paid API does not train on the data, and send only what the task
  needs.
- Evals decide. Every claim that something is better needs a number next to it, and
  the fictional sample set stays as the reproducible public test.
- No new dependency without telling me why, but do propose one when it genuinely
  helps (PDF reading is a fair candidate).
- The code has to read well: this repo is a public portfolio project.

## Questions I want answered before building

1. How many labelled queries are enough for the real corpus to say anything, given
   that 10 dev queries made one query worth 10 points?
2. Chunking: what breaks first, vacancies that are too long for the embedder, or
   CVs that mix five jobs into one vector?
3. Should the explanation run per vacancy (expensive, better) or once over the top
   few (cheaper)? What does that cost per CV at current prices?
4. Is a made-up CV good enough to keep the repo runnable for others, or does the
   difference between my real CV and a made-up one distort the eval?

## Not in this milestone

Agents and tool loops (phase 3), the MCP server (phase 4), the web UI and deploy
(phase 5). Fine-tuning a model is not on the table at this scale; prompt work and
better retrieval come first.
