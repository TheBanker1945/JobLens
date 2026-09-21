# JobLens

AI assistant for the Dutch job market: extracts structured data from vacancies,
matches them to a CV (RAG), runs agents, and exposes tools via MCP.
Public repo, portfolio project.

## Purpose

This is a LEARNING + portfolio project. The owner wants to understand every part
conceptually, not just have working code.

## Roles

- Mahdi is the ARCHITECT: he decides what gets built and why, reviews, runs and
  understands it. He does not need to learn to write the code.
- Claude Code is the ENGINEER: it writes all the code.

## How to work with me

- Before implementing, give a short plan: what you'll build, which files, and the
  design choice + one alternative you rejected and why. Wait for my OK.
- After implementing, explain how it works in plain language (data flow, not line
  by line) and tell me how to run and verify it myself.
- Keep changes small: one milestone step at a time.
- Prefer no frameworks until we've built the thing by hand once.
- If something I ask for is a bad idea, say so and explain the trade-off.

## Git (I am learning git — teach as we go)

- One branch per milestone (e.g. feat/1.1-raw-call), merged via a pull request.
- Show and briefly explain each git command you run.
- Suggest when a commit makes sense, with a conventional commit message.
- Never force-push, never rewrite pushed history, never commit directly to main.

## Tech

- Python 3.12, uv, pydantic v2, httpx, openai SDK (as generic OpenAI-compatible
  client), pytest, ruff
- Provider-agnostic LLM layer: provider, model, base_url and api_key come from
  config/env, never hardcoded. Must work with Ollama, LM Studio, OpenRouter,
  Gemini, DeepSeek, Claude and OpenAI.
- Default for vacancy extraction (public data): Gemini, model gemini-3.8-flash,
  thinking off. Chosen by the eval in milestone 1.5 (docs/learning-log.md); re-check
  when its introductory price ends (2026-12-31) or a new model appears.
- Local fallback: Ollama on Windows, reachable from WSL at http://localhost:11434/v1,
  model qwen3:8b. Keep it working and in the eval.
- Reasoning/"thinking" must be switchable per model, via verified profiles in
  src/joblens/llm/providers.py. Use exact model IDs, never "-latest" aliases.
- Personal data (CVs) may go to cloud models when that makes the app measurably
  better (Mahdi's decision, 2026-09-21). Local-only is no longer the rule; being
  over-cautious at the cost of quality is not wanted. Conditions: the choice stays
  visible and configurable (its own settings prefix, default written down, README
  says where CV text goes), prefer providers whose paid API does not train on the
  data, and only send what the task needs.
- That settings prefix is CV_* (default gemini-3.8-flash, local Ollama block
  commented in .env.example). "Only what the task needs" is src/joblens/cv/clean.py:
  e-mail, phone, address, postcode, date of birth, links and id numbers go; city
  and nationality stay because a vacancy can require them; the name goes only when
  --strip-name says exactly what it is.
- A CV reaches the index as its whole redacted text, one query (3.5, measured on
  four invented CVs: best worst-case nDCG@10 of five representations, and the
  cheapest). Splitting it per job or per chunk lost. Do not reopen without a CV
  long enough for dilution to be real. The local embedder is not a 13-point drop
  for CV matching but a cliff: worst case 0.48 against 0.76.
- A CV, a vacancy and a query must share one embedding space, so opening CVs to the
  cloud also opens cloud embeddings for the whole corpus. Re-measured on the 202
  real vacancies in milestone 3.1: the embedder is now **gemini-embedding-2** with
  the **structured** document style (holdout 88% hit@1 / 0.91 MRR, against 75% /
  0.88 for qwen3-embedding:0.6b on raw text). Ollama stays the local alternative,
  in the eval and one edit of .env away, and costs about 13 points of hit@1.
- Do not carry a retrieval conclusion from data/samples/ to the real corpus. In 3.1
  every 2.2 answer changed sign: gemini-2 on raw text was perfect on the ten
  fictional vacancies and came last on the real ones. Ten invented vacancies have
  no shared employer boilerplate and no junk postings, and both decide the result.
- Embedding models truncate silently. Measured 2026-09-20: Ollama serves
  qwen3-embedding:0.6b with a 4,096-token window (~22,000 chars), whatever the
  model card says; gemini-embedding-001 cuts at ~10,500 chars. Check
  VacancyIndex.oversized() before assuming a long document was read.
- Planned (later): a web UI to choose model and thinking, explaining what each
  choice changes (accuracy, hallucinations, speed, cost) using eval results.

## Vacancy sources

- Five sources behind one `VacancySource` interface in src/joblens/sources/;
  adding one is an adapter file plus a few lines in sources.toml.
- Recruitee, Greenhouse and jobdataapi are public APIs and need nothing.
- Indeed and LinkedIn are scraped through JobSpy, an optional dependency:
  `uv sync --group scrape`. Pinned to a git commit on purpose — its last release
  requires numpy 1.26, and resolving it from PyPI silently installs a 2024
  version.
- JobSpy is used for listings only. LinkedIn descriptions are fetched by us,
  paced, because its own description loop has no delay and swallows errors.
  LinkedIn is off by default in sources.toml; turning it on is a deliberate
  choice about your own IP.
- Scraping never runs while someone is using JobLens: scripts/daily_update.sh
  fetches on a schedule, and search only reads what is already stored.
- Every fetch writes a report to data/raw/runs/ and exits non-zero when a source
  looks broken, throttled, or suspiciously empty.

## Project layout

- src/joblens/ package code
- src/joblens/llm/ provider-agnostic LLM layer
- scripts/ small runnable scripts for experiments
- data/samples/ own example vacancies (committed)
- data/raw/ scraped or personal data (never committed)
- docs/ learning log and notes
- tests/

## Commands

- uv run pytest
- uv run ruff check . && uv run ruff format .
- uv run --group scrape python scripts/fetch_vacancies.py   # fetch new vacancies
- uv run python scripts/index_vacancies.py                  # extract, then embed
- uv run python scripts/search_vacancies.py "query" --corpus raw
- uv run python scripts/data_status.py                      # freshness and health
- uv run python scripts/read_cv.py <cv.pdf|.md|.txt>        # read and redact a CV
- uv run python scripts/match_cv.py <cv> --top 10           # rank vacancies for a CV
- uv run python scripts/eval_cv_matching.py                 # which CV style wins

## Rules

- Never commit secrets, .env, or anything from data/raw/.
- Conventional commits: feat:, fix:, chore:, docs:, test:, refactor:
- No new dependency without telling me why it's needed.
