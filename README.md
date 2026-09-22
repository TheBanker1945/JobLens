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
not by taste: sending the **whole redacted CV as one query** beat a structured
profile, one query per job, chunking, and having a model write the advert the
person would be hired for next. The table is in the 3.5 entry of
`docs/learning-log.md`.

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
```

Scores are comparable **inside one list and nowhere else** — not between two CVs,
not between two runs over a corpus that has changed, not across prompt versions.
Every run prints a stamp saying which of those it was.

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

Swapping either for the commented-out Ollama block keeps that data on your own
machine. For embeddings that costs about 13 points of hit@1, measured; what it
costs for CV work is measured in milestone 3.6. A CV, a vacancy and a query have
to share one vector space, so `EMBED_*` is necessarily one choice for all three.

## License

MIT. See [LICENSE](LICENSE).
