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
  src/joblens/storage/: FileStore (data/raw/) for the scripts and evals, and
  PostgresStore, one per user, for the web app (7.2). A run is addressed by an
  id, never by a path. Labels for a real CV live in data/raw/cv-labels/ and are
  private by default; only the four invented CVs' labels are committed, as
  evidence, and they are never imported into anybody's account.
- The database is Postgres (decided 2026-09-25, Supabase ruled out): Docker
  locally (compose.yaml, 127.0.0.1:54320, container and volume `joblens-db`
  shared by every worktree), Neon Frankfurt when hosted. Plain SQL through
  psycopg, no ORM. A schema change is a new numbered file in
  src/joblens/storage/schema/, never an edit to an applied one (the runner
  refuses). Every table holding a person's data references users ON DELETE
  CASCADE, so deleting a user deletes everything. Every PostgresStore query is
  scoped by user_id. Uploaded CV files live in cv_files and expire after 30
  days (KEEP_ORIGINAL); the redacted text stays. The vacancies a hosted server
  ranks are published into the database (7.8.1, storage/published.py,
  scripts/publish_corpus.py): exactly load_corpus("raw", open_only=True)
  .extracted(), in its order, with vectors keyed as the local cache keys them;
  `api.py --corpus db` reads them. Public adverts, no user_id -- and never a
  vector derived from a CV (LayeredVectors keeps those in the server's local
  cache). The hosted app is the Dockerfile running scripts/hosted.py (7.8.2):
  settings only from the environment (never a .env), vacancies from the
  database, Secure cookies unless every JOBLENS_HOSTS entry is local, no local
  model providers, no access log of its own. .dockerignore keeps data/ and
  .env out of the image; keep it that way, and .gcloudignore keeps them from
  being uploaded at all (folder rules start with "/", or they match inside
  src/ too; check `gcloud meta list-files-for-upload .` before a deploy).
  Deployed (7.8.4): Cloud Run service `joblens`, europe-west4, service
  account `joblens-run` (reads its three secrets only). On Cloud Run it needs
  --no-cpu-throttling (matches run after their request answers) and
  --max-instances 1 (a starting server marks open matches interrupted).
  The privacy page (7.8.3, /privacy, privacy.* keys) states what the code
  does: what is kept, where it goes, retention (file 30 days, link 7, session
  30), rights. Change it in the same commit as any change to those facts. A
  hosted server purges expired files, links and sessions by itself
  (AppConfig.purge_every); JOBLENS_OPERATOR and JOBLENS_CONTACT name who runs it.
  Tests use the joblens_test
  database, rebuilt from this checkout's migrations at the start of every run
  (worktrees on different branches share it), and skip without Docker.
- Preferences (7.3, src/joblens/preferences/) are stated answers, never
  inferred, and kept apart from the CV. Facts extraction fills (contract,
  hours, work mode, distance, salary, languages, title level, employer) are
  applied in code before the shortlist is cut: a contradiction moves a vacancy
  behind those with fewer, never removes it, and unknown costs nothing; the run
  records `before` and `conflicts` per vacancy. Years, degree and sectors go to
  the holistic judge as a block after the CV, only when answered -- without
  one the prompt is 3.6 to the byte. A distance limit has a margin (p2,
  Mahdi 2026-09-29): a quarter more, at least 5 km (rerank.allowed_km); no
  other answer has one. PREFERENCES_VERSION ("p2") covers both
  halves; bump it when either changes. Work status and goals (7.9.2) are
  saved only (SAVED_ONLY): no rule reads them and they are outside is_empty
  and stamp, until a measurement says they may act. Distance is straight-line km between
  PDOK place centroids (places_nl_coordinates.csv). Not measured on Mahdi's
  labels until he answers the questionnaire.
- Matching a CV is a call, not a script: src/joblens/service/ (`rank`, then
  `judge`; 7.1). Scripts and the coming API are its callers. A service takes
  settings (`Models`), data (a path or a `CVFile` upload) and a store, and
  raises only `ServiceError`s; it never reads .env and never prints, because a
  user's own key arrives as settings. Phase 7 (the web app) is planned and its
  decisions recorded in docs/web-app-phase-7.md.
- The web API (7.4, src/joblens/api/, scripts/api.py) is FastAPI around
  service calls; routes are plain `def` (blocking clients) and hold no logic.
  It refuses a foreign Host, and every non-GET without `X-JobLens: 1` (the CSRF
  guard, with SameSite=Lax cookies). Signing in (7.5) is invite-only: owner and
  tester roles, one-time login links (7 days) made by `scripts/db.py invite`
  or the owner's Settings page (7.9.1; a page makes testers only, and a new
  link replaces one not yet used), 30-day sessions in an HttpOnly cookie, and
  only SHA-256 hashes of links and
  sessions in the database. The token sits after `#` in a link so it never
  reaches a server log, and opening a link shows a button rather than signing
  in (chat previews would use it up). Cookies must be Secure anywhere but
  127.0.0.1 (create_app refuses otherwise). No dev-user bypass exists; do not
  add one.
- The user-facing UI (7.7) is style A "Helder" (Mahdi's pick of three
  mockups, 2026-09-26): plain HTML/CSS/JS in src/joblens/api/ui/, served by
  the FastAPI app, no build step, no framework. Pages carry a strict CSP
  (script/style/font from 'self' only): no inline script, no style="",
  no on*= handlers, no outside CDN or Google Fonts (the font is self-hosted,
  OFL). Scraped text goes in as text only -- never innerHTML -- and only
  http(s) links are followed. Elements are filled with fill() from dom.js,
  never replaceChildren() (which writes null and false as words; a test
  forbids it). Five languages: en, nl, de, fr, es (informal; de/fr/es written
  by Claude, not yet read by a native speaker). Every text is a key in ui/assets/i18n/<code>.json
  with identical keys and placeholders in every language (tests enforce it);
  the language is picked server-side (api/language.py: saved choice, then the
  browser's languages, then its country, then English). tests/test_ui.py holds
  all of this. Pages and every /api/ answer are `Cache-Control: no-store`
  (they carry CV text). A new account is sent to /guide until it finishes or
  skips it (users.onboarded_at) or has a CV. The preferences form checks what
  it can in the page, in the person's language, but the server's schema
  decides; keep the form's limits equal to preferences/schema.py.
- Bring your own AI (7.6): a person may store one key for the `cv` role only
  (embeddings stay the operator's: one vector space). Providers come from the
  fixed list in src/joblens/llm/presets.py -- never a user-typed base URL
  (SSRF); local ones only when allow_local_providers (the server is on the
  user's machine). A key is tested with one call before it is stored,
  encrypted with JOBLENS_SECRET_KEY (joblens/vault.py, Fernet; never hand-roll
  crypto), never returned (a 4-character hint only), and never exported. A
  model is suggested only where measured; never invent model ids. Every paid
  call is recorded in `usage` with whose key paid; testers on the operator key
  have JOBLENS_TESTER_MONTHLY_USD (default 1.00) and all testers together
  JOBLENS_OPERATOR_MONTHLY_USD (10.00), checked before a paid step (402 when
  spent); the owner and own keys are never stopped. A match is a job (jobs table, one open per person,
  enforced by a unique index), run in a thread (api/runner.py) by
  service/jobs.py, which always ends done or failed with a sentence; a restart
  marks open jobs interrupted. An upload is read, redacted and profiled once
  (service/cvs.py, 20 MB limit: real CVs reach 12.5 MB) and later matches use
  the stored text and profile, never the file.
- A mark in the viewer requires a reason and is stored verbatim with what the
  judge said at the time. Never infer a rule from a pattern of answers and write
  it down as if the user had stated it. The web app's marks (7.7.3,
  service/marks.py, POST /api/marks) are the same `Decision`s in the same
  labels: appended, never overwritten, checked against the run that was on
  screen (not the open corpus, so a closed vacancy can still be marked).
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
- Scraping never runs while someone is using JobLens: it runs on a schedule,
  and search only reads what is already stored. Since 7.8.5 that schedule is
  in the cloud, not on a laptop (Mahdi: "everything should be cloud based",
  JobSpy/Indeed included, tried from a datacenter): Cloud Run job
  `joblens-nightly` (scripts/nightly.py, the Dockerfile's `nightly` stage)
  started by Cloud Scheduler at 03:00 Europe/Amsterdam, fetch -> index ->
  publish to Neon. Its disk is the bucket `joblens-state-883656455192`
  (europe-west4, versioned 30 days): vacancy state only, by the allow-list in
  src/joblens/cloud/state.py -- a CV, label or run never goes there. The job
  is never retried automatically (a retry would ask every site again). It
  fetches only while the owner's switch is on (settings page, app_settings
  'nightly_fetch', default OFF, Mahdi 2026-09-29); --force overrides it for a
  run by hand, --no-fetch only indexes and publishes. The embedding client
  waits out a 429 (a minute, up to five times): per-minute quotas count every
  text in a batch. The
  live app takes up a new publish within 15 minutes, without a restart.
  daily_update.sh stays for a run by hand on a laptop; the bucket, not
  data/raw/, is now the vacancies' source of truth.
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
- docker compose up -d db && uv run python scripts/db.py migrate   # the web app's database
- uv run python scripts/api.py                              # the web API, /api/docs
- uv run --group scrape python scripts/fetch_vacancies.py   # fetch new vacancies
- uv run python scripts/discover_boards.py [--accept]       # find employer boards
- uv run python scripts/index_vacancies.py                  # extract, then embed
- uv run python scripts/publish_corpus.py --to NEON_DATABASE_URL  # vacancies to the hosted db
- docker build -t joblens . && docker run -p 8080:8080 --env-file F joblens   # the hosted app
- uv run python scripts/search_vacancies.py "query" --corpus raw
- uv run python scripts/data_status.py                      # freshness and health
- uv run python scripts/read_cv.py <cv.pdf|.md|.txt>        # read and redact a CV
- uv run python scripts/match_cv.py <cv> --top 10           # rank vacancies for a CV
- uv run python scripts/preferences.py ask                  # what you want from a job
- uv run python scripts/eval_cv_matching.py                 # which CV style wins
- uv run python scripts/eval_judge.py                       # is the judge honest?
- uv run python scripts/eval_judge.py --labelled            # judge vs every label, no index
- uv run python scripts/eval_judge_talentclef.py            # judge vs human expert labels
- uv run python scripts/compare_runs.py                     # this run vs the last

## Rules

- Never commit secrets, .env, or anything from data/raw/.
- Conventional commits: feat:, fix:, chore:, docs:, test:, refactor:
- No new dependency without telling me why it's needed.
