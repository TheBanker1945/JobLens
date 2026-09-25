# JobLens

An AI assistant for the Dutch job market. JobLens pulls structured data out of
vacancies, matches them to a CV with retrieval-augmented generation (RAG), runs
agents, and makes its tools available to Claude through MCP.

This is a learning and portfolio project: each part is built by hand first, so the
concepts are clear before any framework is introduced.

## Roadmap

1. **LLM fundamentals:** a provider-agnostic client and structured extraction from
   Dutch vacancies, plus a small eval comparing models
2. **Embeddings & RAG from scratch:** semantic search, and CV-to-vacancy matching
   with citations
3. **Tool use & agents:** an agent loop written by hand, then compared with a
   framework
4. **MCP:** a Python MCP server exposing JobLens tools to Claude
5. **Portfolio polish & deploy:** a web interface (including choosing the model and
   thinking, with the trade-offs explained using eval results), deployed on Cloud Run

## Works with any LLM provider

JobLens talks to models through the OpenAI-compatible API. Provider, base URL,
model and API key come from environment variables and are never hardcoded, so the
same code runs against Ollama, LM Studio, OpenRouter, Gemini, DeepSeek, Claude or
OpenAI.

Which model to use is decided by an eval (`scripts/eval_extraction.py`), not by
guesswork. Current choice:

- **Vacancy extraction** (public data): Gemini `gemini-3.8-flash`, thinking off.
  In the milestone 1.5 eval it made no invented values, at about 1.6 s and $1.68 per
  1,000 vacancies.
- **Local fallback:** Ollama with `qwen3:8b`.
- **CVs and other personal data:** local by default today; cloud models are allowed
  when they measurably improve matching. Where CV text goes is a configured,
  documented choice, never a silent one.

## Setup

Requirements: Python 3.12 and [uv](https://docs.astral.sh/uv/).

```bash
git clone https://github.com/TheBanker1945/JobLens.git
cd JobLens
uv sync                  # creates .venv and installs the exact versions from uv.lock
cp .env.example .env     # then fill in your provider settings
mkdir -p data/raw        # local-only folder for personal data (see below)
```

With Gemini (the default for vacancy extraction), `.env` looks like this:

```env
GEMINI_API_KEY=your-key
LLM_PROVIDER=gemini
LLM_BASE_URL=https://generativelanguage.googleapis.com/v1beta/openai
LLM_API_KEY=${GEMINI_API_KEY}
LLM_MODEL=gemini-3.8-flash
LLM_THINKING=false
```

For the local Ollama fallback:

```env
LLM_PROVIDER=ollama
LLM_BASE_URL=http://localhost:11434/v1
LLM_API_KEY=ollama
LLM_MODEL=qwen3:8b
LLM_THINKING=false
```

## Development

```bash
uv run pytest                                  # run the tests
uv run ruff check . && uv run ruff format .    # lint and format
```

The database tests need the local Postgres below; without it they are skipped
and everything else still runs.

## The database (for the web app)

The web app keeps its users, their CVs, runs, labels and preferences in
Postgres: in Docker on your own machine, and on [Neon](https://neon.com)
(Frankfurt, free tier) once it is hosted. The same migrations run on both; only
`DATABASE_URL` changes. The scripts and evals keep working on the files in
`data/raw/` and need none of this.

```bash
docker compose up -d db                        # Postgres 17 on 127.0.0.1:54320
uv run python scripts/db.py migrate            # create or update the tables
uv run python scripts/db.py create-user you@example.com --name "You" --locale en
uv run python scripts/db.py import you@example.com   # copy your runs and labels in
uv run python scripts/serve.py --db you@example.com  # the viewer, over the database
```

Copy the `DATABASE_URL` line from `.env.example` into `.env` first. The schema is
plain SQL in `src/joblens/storage/schema/`, one numbered file per change.
Deleting an account (`scripts/db.py delete-user`) removes everything it stored
in one statement, and an uploaded CV file is kept 30 days
(`scripts/db.py purge-files`); its redacted text stays until the account goes.

## Where the vacancies come from

Five sources, one interface. Adding another is one adapter file plus a few lines
in `sources.toml`; nothing downstream knows the difference.

| Source | How | Needs |
|--------|-----|-------|
| Recruitee, Greenhouse | public JSON from a company's own board | nothing |
| jobdataapi | aggregator, anonymous tier (~10 requests/hour per IP) | nothing |
| Indeed | its mobile app API, through [JobSpy](https://github.com/speedyapply/JobSpy) | `uv sync --group scrape` |
| LinkedIn | its logged-out guest endpoints, off by default | `uv sync --group scrape` |

Scraping never happens while someone is using JobLens: a scheduled run puts
vacancies in `data/raw/`, and search only ever reads what is already there.

```bash
uv run --group scrape python scripts/fetch_vacancies.py   # fetch what is new
uv run python scripts/index_vacancies.py                  # extract, then embed
uv run python scripts/search_vacancies.py "zorg voor ouderen" --corpus raw
uv run python scripts/data_status.py                      # how fresh is all this?
```

Every fetch writes a report to `data/raw/runs/` and exits non-zero when
something looks wrong -- a source that found nothing at all, descriptions that
never arrived, a board refusing us -- because all three otherwise look exactly
like a quiet day.

### Keeping it fresh automatically

`scripts/daily_update.sh` fetches and then indexes, logs to `data/raw/logs/`,
and stays silent unless something went wrong.

On WSL, cron only runs while WSL itself is running, so Windows Task Scheduler is
the more reliable of the two:

```powershell
# Windows Task Scheduler, runs daily at 07:15 even with no terminal open
schtasks /create /tn "JobLens update" /sc daily /st 07:15 ^
  /tr "wsl.exe -d Ubuntu -- bash -lc 'cd ~/repos/JobLens && ./scripts/daily_update.sh'"
```

```bash
# or, inside WSL: sudo service cron start, then crontab -e
15 7 * * * cd ~/repos/JobLens && ./scripts/daily_update.sh
```

## Project layout

```text
src/joblens/     package code
scripts/         small runnable experiments and the scheduled update
data/samples/    example vacancies and CVs, all invented (committed)
data/raw/        scraped or personal data (never committed)
docs/            learning log and notes
tests/           tests
```

## Reading a CV

```bash
uv run python scripts/read_cv.py data/samples/cvs/lisa_de_vries.pdf
uv run python scripts/read_cv.py your_cv.pdf --strip-name "Your Name" --show-sent
uv run python scripts/read_cv.py your_cv.pdf --no-extract      # sends nothing
```

A CV is read from PDF, plain text or markdown, stripped of everything matching
does not need, and turned into a structured profile: jobs with dates and skills,
education, certificates, languages. Three invented CVs are committed in
`data/samples/cvs/`, so the whole thing runs after a clone.

**What is removed before anything is sent:** e-mail addresses, phone numbers,
street addresses, postcodes, dates of birth, links, and bank or citizen service
numbers. The script prints every removal, and `--show-sent` prints the exact text
that would leave the machine. Your **city** is kept on purpose -- it decides
whether a job is commutable. Your **name** is kept unless you pass
`--strip-name`, because finding a name in free text without being told it means
guessing, and a wrong guess deletes a skill instead.

Reading a scanned CV fails loudly rather than quietly matching an empty document.

## Matching a CV against the vacancies

```bash
uv run python scripts/match_cv.py data/samples/cvs/sanne_vermeulen.md
uv run python scripts/match_cv.py your_cv.pdf --strip-name "Your Name" --top 20
uv run python scripts/eval_cv_matching.py        # which representation is best
```

The CV and the vacancies are embedded into the same space and ranked by cosine
similarity. How a CV should be turned into a query was decided by measurement,
not by taste. In 3.5 the **whole redacted CV as one query** beat a structured
profile, one query per job, chunking, and having a model write the advert the
person would be hired for next (the table is in `docs/learning-log.md`). On
the grown corpus of 400 vacancies the best answer is **both**: the whole CV and
that advert each rank every vacancy, and the two lists are fused by position
(reciprocal rank fusion), which lifted the worst CV's nDCG@10 from 0.29 to 0.51
(`evals/cv-matching.toml`). `--style raw` is the one-query way.

Each shortlisted vacancy is then read by a model next to the CV, which returns a
verdict (strong / possible / weak), the lines of the CV that answer the vacancy,
and what the vacancy asks for that the CV does not show.

**Every quote is checked against the source before you see it.** A quote that is
not in the CV, or not in the vacancy, is deleted together with the claim it
supported — so a claim you read is a claim backed by text that exists. Measured
over four CVs and forty vacancies: 244 quotes, 100% found, and a CV that fits
nothing in the corpus gets ten "weak" verdicts rather than a polite list.

```bash
uv run python scripts/match_cv.py your_cv.pdf --top 20   # ~0.36 cent per vacancy
uv run python scripts/match_cv.py your_cv.pdf --no-explain   # retrieval only
uv run python scripts/eval_judge.py                      # is it honest? does it agree?
uv run python scripts/compare_runs.py                    # this run against the last
```

### What keeps coming up that you do not have

Across a whole run, the per-vacancy gaps are counted into the requirements that
recur, weighted by how good each match was (`fit / 100`, summed) and traced back
to a quote from one of the vacancies asking for it. **No extra model call**: the
grouping uses the skill names already extracted from the corpus, so nothing on
screen is a label a model invented about its own earlier answers. How much of a
run this can group is printed next to it. Three further checks — education level,
required languages, years — come straight from extracted fields and involve no
judge at all.

### What you want: preferences

```bash
uv run python scripts/preferences.py ask                  # eleven questions, all optional
uv run python scripts/match_cv.py data/raw/cv/you.pdf --preferences
```

A CV says what you have done; preferences say what you want next, and the two
are kept apart. Contract, hours, work mode, distance from home (as the crow
flies), salary, languages, level and employers to avoid are checked against
what each advert states: a vacancy that contradicts one moves back, behind the
ones that do not. **Nothing is ever removed**, a vacancy that does not state
something is never moved for it, and the run keeps where each one was and why
(the viewer marks it "moved from #3"). Whether you apply when a vacancy asks
more years or a higher degree than your CV shows, and sectors to avoid, are
told to the judge instead, because they need reading rather than a field.

### When your CV fits nothing

It says so, in the first line, and then still shows the closest few with the
reason each one fails and the gap summary. The answer is built on the verdicts,
not on the similarity score: a cosine separates a CV that fits nothing from one
that fits by about 0.12, which is not a threshold, while the judge reading the
vacancy gives the same CV zero strong, zero possible and ten weak.

### Comparing two runs

```bash
uv run python scripts/compare_runs.py            # the two newest runs of one CV
uv run python scripts/compare_runs.py --list
```

Scores are comparable **inside one list and nowhere else** — not between two CVs,
not across embedders, judge models or prompt versions. Every run is stored under
`data/raw/cv-runs/` with a stamp of all of those, and `compare_runs.py` refuses
to compare two runs whose stamps disagree, naming what moved. A corpus that has
merely gained vacancies is the one difference it will compare across, because it
changes by itself — it reports what entered and left the shortlist instead.

## The web API

```bash
uv run python scripts/api.py          # http://127.0.0.1:8001/api/docs
```

What the web app's page will call: upload a CV (read, redacted and profiled
once, then kept), answer the preferences, start a match, follow it, read the
runs. A match runs as a background job (20-60 s) whose progress is kept in the
database, and one person can have one match running at a time. `/api/docs` is
an interactive page of every route, generated from the code, where all of it
can be tried by hand (click "Authorize" and enter `1` first). Built on FastAPI, on the database above.

**Signing in is invite-only.** There is no sign-up and no password:

```bash
uv run python scripts/db.py invite you@example.com --owner   # prints a login link
uv run python scripts/db.py invite tester@example.com        # send it to them yourself
```

A link works once, within 7 days, and gives a 30-day session (an HttpOnly
cookie). Only a hash of each link and session is stored. Every person reaches
only their own CVs, runs and matches, can download all of it
(`GET /api/me/export`) and can delete all of it (`POST /api/me/delete`). Every
request that changes something must carry the header `X-JobLens: 1`, which
another website cannot make your browser send.

**Your own AI, or ours within an allowance.** Without a key of your own, your
CV is read and your matches judged by JobLens's model (Gemini), and a tester
has a monthly allowance on it (default $1, about 25 matches; all testers
together $10). With your own key -- Gemini, OpenAI, Anthropic, OpenRouter or
DeepSeek, or a local Ollama or LM Studio when JobLens runs on your machine --
there is no limit, and your CV text goes to that provider instead. The key is
tested with one small call before it is kept, stored encrypted with the
server's `JOBLENS_SECRET_KEY` (`scripts/db.py new-secret`), and never shown
again. The embedding model stays JobLens's either way: a CV is only comparable
with vacancies embedded by the same model. `GET /api/usage` shows what you
spent.

## The viewer: reviewing a run, and marking what it got wrong

```bash
uv run python scripts/serve.py --judged-by "Your Name"   # http://127.0.0.1:8000
```

A local page over the runs that have been stored. It splits a run into three:
what it **recommended**, what a model **read and turned down**, and what
retrieval **never showed anybody** — which on a real run is 267 of 279 vacancies,
and is the rejection that had no score, no position and no record at all until
the whole ranking started being stored. The vacancy text and the extracted fields
are one click away, because a rejection is checked against the advert rather than
against a summary of it.

Marking a vacancy ("would apply" / "might" / "no") **requires a one-line
reason**, stored exactly as typed next to what the judge had said about it at
that moment. That is the whole point: a label without a reason cannot tell you
whether the system or you was wrong, and `eval_judge.py` now prints every
disagreement with your own sentence beside it. Nothing infers a rule from a
pattern of answers.

Built out of the standard library — routing, JSON and static files are about a
hundred lines of `http.server`, the page is plain DOM with no build step, and no
dependency was added for it. It binds to 127.0.0.1 only, has no login, and starts
nothing: no model is ever called from a page.

## Data & privacy

`data/raw/` is in `.gitignore` and is **never committed**. It holds scraped
vacancies and personal data such as CVs.

Personal data may go to a cloud model when that measurably improves the result
(the owner's decision, 2026-09-21). Two settings control it and both are written
down in `.env.example`:

| Setting | Default | What it sees |
|---------|---------|--------------|
| `CV_*` | `gemini-3.8-flash` | your CV, redacted as described above |
| `EMBED_*` | `gemini-embedding-2` | vacancies, and your CV when it is matched against them |
| your own key (web app, 7.6) | none | your CV and the vacancies it is judged against, if you add one |

Swapping either for the commented-out Ollama block keeps that data on your own
machine. For embeddings that costs about 13 points of hit@1, measured; what it
costs for CV work is measured in milestone 3.6. A CV, a vacancy and a query have
to share one vector space, so `EMBED_*` is necessarily one choice for all three.

## License

MIT. See [LICENSE](LICENSE).
