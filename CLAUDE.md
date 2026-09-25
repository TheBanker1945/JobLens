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
- Runs, labels and preferences are read and written only through
  src/joblens/storage/ (file-backed today). A run is addressed by an id, never by
  a path. Labels for a real CV live in data/raw/cv-labels/ and are private by
  default; only the four invented CVs' labels are committed, as evidence.
- Matching a CV is a call, not a script: src/joblens/service/ (`rank`, then
  `judge`; 7.1). Scripts and the coming API are its callers. A service takes
  settings (`Models`), data (a path or a `CVFile` upload) and a store, and
  raises only `ServiceError`s; it never reads .env and never prints, because a
  user's own key arrives as settings. Phase 7 (the web app) is planned and its
  decisions recorded in docs/web-app-phase-7.md.
- A mark in the viewer requires a reason and is stored verbatim with what the
  judge said at the time. Never infer a rule from a pattern of answers and write
  it down as if the user had stated it.
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
- Explaining a match is an LLM call per shortlisted vacancy (0.36 cent, ~2s), and
  every quote it returns is verified against the CV or the vacancy in code before
  it is shown (src/joblens/cv/judge.py). Never loosen that check into a fuzzy
  match; normalising formatting is safe, accepting a paraphrase is not. Bump
  PROMPT_VERSION when the prompt or the bands change.
- The judge is measured by the order it puts labelled vacancies in
  (`eval_judge.py --labelled`: concordance and nDCG against retrieval's order),
  and against TalentCLEF 2026's human expert labels. Two identical runs of 3.6
  differ by 0.09 concordance on the real CV (6.2), so read every number there
  as a range and beat the noise before believing a change. Prompt 3.7 (lenient
  on years and degrees) did not pass its rule and is not the judge.
- A second judge, `--judge requirements` (src/joblens/cv/requirements.py, 6.3):
  a vacancy's requirements are read once and stored, the CV answers each one
  with a quote, and `score()` adds it up with weights written before the eval
  (years and degrees half, nice-to-haves a quarter). Not the default: it did
  not beat 3.6 on the real CV beyond the noise, and lost on Lisa's
  Claude-written labels. A knockout the CV is silent about is `unknown`, never a
  rule-out; a wish the CV states that the vacancy contradicts is a conflict.
  Changing the weights re-scores stored answers for free (SCORE_VERSION).
- A PDF can carry characters it does not contain: a subset font whose ToUnicode
  map points into the private use area (measured on a real CV, 2026-09-22 — 114
  glyphs, every year in the work history). src/joblens/cv/read.py marks each one
  "?" so a hole stays visible, refuses a file that is more than 10% of them, and
  src/joblens/cv/extract.py nulls any year in a profile that is not in the CV
  text. Never repair this by loosening the quote check in src/joblens/cv/verify.py.
- pypdf reads every PDF; pdfminer.six reads it again only when more than 2% of
  pypdf's words are over 20 characters (words placed without spaces), and its
  text is kept only if that share at least halves (read.py; 2026-09-24: two of
  eleven real PDFs switch, the real CV must not -- pdfminer reads it worse).
- The quote check's only allowance beyond formatting: a hyphen at a line end is
  read as extracted, as a split word, or as a real hyphen (verify.py). Measured
  over 1,925 recorded quotes: nothing that passed before fails.
- A run judges at most two vacancies per employer (`--per-employer`,
  PER_EMPLOYER in cv/match.py); the ranking itself never moves.
- A CV reaches the index as its whole redacted text (3.5, measured on four
  invented CVs: best worst-case nDCG@10 of five representations, and the
  cheapest), and since 2026-09-22 also as the advert a model writes from its
  profile ("wishlist"); the two rankings are fused by position (reciprocal rank
  fusion, DEFAULT_STYLE in src/joblens/cv/match.py). Measured on 400 vacancies
  with a rule written before the run: worst case 0.51 against 0.29 for the
  whole text alone, and 0.52 against 0.29 on the real CV labelled by its owner.
  `--style raw` is the 3.5 behaviour. Splitting it per job or per chunk lost.
  Do not reopen without a CV long enough for dilution to be real. The local
  embedder is not a 13-point drop for CV matching but a cliff: worst case 0.48
  against 0.76.
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

- Six sources behind one `VacancySource` interface in src/joblens/sources/;
  adding one is an adapter file plus a few lines in sources.toml.
- Recruitee, Greenhouse, SmartRecruiters and jobdataapi are public APIs and
  need nothing. The employer boards are listed in boards.toml (committed),
  filled by scripts/discover_boards.py: it reads the links Indeed and
  jobdataapi keep to where they copied a vacancy from, checks each board with
  one request, and `--accept` adds those with work in scope. A person accepts;
  the script never adds a board on its own. Recruitee also runs under employers'
  own domains ("slug" with a dot is a host). SmartRecruiters costs one request
  per vacancy text, so it applies the scope to its listing before asking.
- Indeed and LinkedIn are scraped through JobSpy, an optional dependency:
  `uv sync --group scrape`. Pinned to a git commit on purpose — its last release
  requires numpy 1.26, and resolving it from PyPI silently installs a 2024
  version.
- JobSpy is used for listings only. LinkedIn descriptions are fetched by us,
  paced, because its own description loop has no delay and swallows errors.
  LinkedIn stays off (Mahdi, 2026-09-22): its robots.txt disallows /jobs-guest/
  for every crawler, Googlebot included.
- Every request passes one gate, src/joblens/sources/polite.py, as an httpx
  transport under the client: paced per *site* (all *.recruitee.com boards are
  one site), capped per run, and a refusal (429, 403, a challenge page, a
  redirect to a consent wall) is remembered in data/raw/fetch-state.json, so
  later runs leave that site alone for 12h, doubling up to a week. Settings are
  the [politeness] table in sources.toml. Never add a way around a refusal: no
  proxies, rotating IPs, browser disguise or retries. A refusal marker is added
  only after it was seen on a real response.
- robots.txt governs what is *crawled*: a source that reads web pages passes
  `extensions=CRAWL` (src/joblens/sources/polite.py), and only those requests
  are checked, with protego (RFC 9309, wildcards included; the stdlib parser
  would have allowed what nationalevacaturebank.nl disallows). Crawl-delay can
  slow the gate, never speed it up; an unreadable robots.txt allows nothing. A
  documented public API is used as documented; jobdataapi and SmartRecruiters
  disallow their own documented APIs in robots.txt.
- EURES (src/joblens/sources/eures.py) carries werk.nl's feed: public but not
  documented, paced at europa.eu's Crawl-delay of 10 s. Regions are NUTS 2024
  (Utrecht nl35, Zuid-Holland nl36; the old nl31/nl33 return nothing,
  silently). Title matching, word by word; only nl/en records; no detail
  requests (the text is a ~2,000-char summary everywhere, and the detail only
  adds contact persons). No record names its employer, so the duplicate check
  cannot see an EURES copy of an Indeed or board job: it completes the corpus,
  it should not lead it.
- Workday career sites (src/joblens/sources/workday.py) are read through the
  JSON their own page uses: not a documented API, so the site's robots.txt
  decides, for both /{site}/ and the API path. Rabobank, ING (JVSGBLCOR),
  Heijmans and Thales disallow theirs, so they are not read. A listing that
  stopped short (400 per run, or Workday's paging ceiling) closes nothing.
- Employers' own career sites (src/joblens/sources/careersite.py) are read
  through their sitemap and the schema.org JobPosting on each page
  (src/joblens/sources/jsonld.py); each is a [[careersite]] in boards.toml.
  `place_in_url` is for worldwide sites: a page is fetched only when its URL
  names a place in the scope. A vacancy is named by its page address.
- A page a source paid for and did not store (outside the scope once read, or
  a duplicate) is remembered in sightings.json with the scope's fingerprint
  and not read again until the scope changes (wolfgroep.nl: 102 requests a
  night -> 2).
- Government vacancies come from werkenbijdeoverheid.nl's sitemap
  (src/joblens/sources/overheid.py): the scope runs on the title in each URL
  before a page is fetched, the facts come from the page's dataLayer, and the
  sitemap closes jobs like a board.
- Phase 5 (docs/vacancy-sources-phase-5.md) widens the sources. Scope decided
  2026-09-22: Zuid-Holland, Noord-Holland, Utrecht and Zeeland, software and AI
  engineering and similar. src/joblens/sources/scope.py applies it to *new*
  vacancies before they are stored (so before anything is paid to index them):
  the place is looked up in the CBS list of all 2,502 Dutch places
  (places_nl.csv, refreshed by scripts/update_places.py), the title against the
  word lists in the [scope] table of sources.toml. An unknown place is kept. It
  never removes stored vacancies that labels refer to; `--no-scope` stores all
  Dutch jobs. Change a word list only with a measurement: run it over the store
  and Mahdi's labels, and every "would apply" must stay in.
- Vacancies close; the store never forgets. data/raw/sightings.json
  (src/joblens/sources/sightings.py) records when each was last listed. An
  employer board lists every job it has, so a stored job it stops listing has
  closed (only after a successful, non-empty fetch). A search job is open while
  a search listed it in the last 7 days or it is at most 30 days old; age alone
  is wrong, Indeed re-lists jobs 200+ days old as new. A board listing a job we
  hold as another source's copy keeps that copy open; it never reopens a job
  its own board closed. Matching and search use `load_corpus(open_only=True)`;
  the evals keep the default (everything), so a closed labelled vacancy cannot
  move their scores.
- Scraping never runs while someone is using JobLens: scripts/daily_update.sh
  fetches on a schedule, and search only reads what is already stored.
- Every fetch writes a report to data/raw/runs/ and exits non-zero when a source
  looks broken, throttled, refused, or suspiciously empty.

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
- uv run python scripts/discover_boards.py [--accept]       # find employer boards
- uv run python scripts/index_vacancies.py                  # extract, then embed
- uv run python scripts/search_vacancies.py "query" --corpus raw
- uv run python scripts/data_status.py                      # freshness and health
- uv run python scripts/read_cv.py <cv.pdf|.md|.txt>        # read and redact a CV
- uv run python scripts/match_cv.py <cv> --top 10           # rank vacancies for a CV
- uv run python scripts/eval_cv_matching.py                 # which CV style wins
- uv run python scripts/eval_judge.py                       # is the judge honest?
- uv run python scripts/eval_judge.py --labelled            # judge vs every label, no index
- uv run python scripts/eval_judge_talentclef.py            # judge vs human expert labels
- uv run python scripts/compare_runs.py                     # this run vs the last

## Rules

- Never commit secrets, .env, or anything from data/raw/.
- Conventional commits: feat:, fix:, chore:, docs:, test:, refactor:
- No new dependency without telling me why it's needed.
