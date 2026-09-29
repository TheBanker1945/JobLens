# 7.8 — Hosting: what is ready, what needs a decision, what needs Mahdi's accounts

Written 2026-09-28, after 7.7 was built (PRs #62-#64). Phase 7's plan
(docs/web-app-phase-7.md) lists 7.8 as: container, hosted database and
storage, secrets, scheduled fetching, privacy notice, delete-my-data, cost
caps. Nothing here is built yet: two decisions and three accounts come first.

## Already built with hosting in mind

- **Cookies and hosts.** create_app refuses cookies that are not Secure on any
  host but 127.0.0.1, and answers only Host headers it was told about.
- **The CSRF guard** (X-JobLens on every change), a strict Content-Security-
  Policy on every page, `Cache-Control: no-store` on pages and API answers.
- **Delete my data and download my data** (7.5 routes; the settings page in
  7.7.4).
- **Tester budgets in code** (7.6): $1 a tester and $10 for all testers, a month.
- **The database is one variable** (DATABASE_URL). Neon is plain Postgres, and
  migrations run on start under an advisory lock, so two containers starting
  at once cannot both apply one.
- **No lasting disk assumed since 7.2**, with one exception: the subject of
  decision 1.

## Decision 1: where the vacancies and their embeddings live

Today the server loads them at start from this machine's disk: data/raw
(vacancies, extracted facts, sightings) and data/cache (embeddings). A hosted
container has no lasting disk. Measured 2026-09-28:
- the stored vacancies are 18 MB of JSON lines;
- a gemini-embedding-2 vector is 12.5 KB (3,072 numbers), so the 1,200-1,500
  open vacancies need 15-19 MB, or about 8 MB at half precision;
- the local cache is much larger (332 MB) only because it keeps every
  experiment's model and document style.

| | A. in Postgres (recommended) | B. baked into the image |
|---|---|---|
| how | tables for vacancies, facts and vectors; the nightly fetch writes them; the server reads them at start | the nightly job copies a snapshot into a new image and redeploys |
| size | about 30-40 MB: fits Neon's free 0.5 GB with room for years of accounts | the image grows by about 40 MB |
| work | a milestone: a storage seam for the corpus, like 4.2 was for runs | little code |
| risk | one source of truth; any host; several containers at once | a failed night deploy leaves yesterday's vacancies; data/raw also holds personal files (CV labels, CV runs, Mahdi's CV) that a list must keep out of the image forever |

**Recommendation: A.** It is what "no lasting disk" meant, and it takes the
laptop out of the picture. B is faster to ship, but it puts personal files one
wrong line away from an image in a registry.

## Decision 2: where the nightly fetch runs

- **A Cloud Run job, started by Cloud Scheduler**, in the same Google Cloud
  project. Recommended with 1A: no machine has to be on.
- **Mahdi's machine** (scripts/daily_update.sh, as today), writing to Neon. No
  new service, but the laptop must be on at night and holds the Neon URL.

The rules stay the same either way: the politeness gate, robots.txt, and never
fetching while someone is using JobLens.

## Decision 3: an address

Cloud Run gives every service a free https address (*.run.app), which is
enough for testers. A domain of his own is optional and can come later: it
changes one setting (the allowed host), not the code.

## What only Mahdi can create

1. **Neon**: a project in Frankfurt (AWS eu-central-1). Its connection string
   becomes DATABASE_URL. The free plan suffices. Projects idle for 90+ days may
   be deleted, which a nightly fetch prevents.
2. **Google Cloud**: a project with billing, and in it:
   - Cloud Run in europe-west4 (Eemshaven, NL);
   - Artifact Registry for the image;
   - Secret Manager for DATABASE_URL, GEMINI_API_KEY and JOBLENS_SECRET_KEY;
   - with 2A, Cloud Scheduler.
3. **Cost caps on the Gemini key itself**: a quota on requests per day in the
   Cloud console, and a budget alert. An alert only warns; the quota stops.
   JobLens's own limits (7.6) are only as good as its code, so both are wanted.
4. **The privacy notice's two facts** (GDPR art. 13): whose service this is,
   and how to reach them. Everything else can be written from the code:
   - what is kept: the redacted CV text; the uploaded file for 30 days;
     preferences; marks with their reasons; usage;
   - where it goes: Google's Gemini API for CV text (a paid API that does not
     train on it), Neon for storage, Google Cloud Run to run it;
   - for how long, and the rights already built: download and delete.

## Decided and done

- **2026-09-29: Mahdi went with the recommendations** (1A, 2A, run.app) and
  created the Neon project: Postgres only (no object storage, functions, AI
  gateway or Neon Auth), Frankfurt, Postgres 18.6. Migrations 0001-0005 ran on
  it after a trial in a throwaway schema, and the whole test suite passed
  against a temporary database there (924 tests).
- **7.8.1, the vacancies in Postgres**: migration 0006, published from this
  machine in 3 s; a CV ranks identically from Neon and from the files
  (learning log 7.8.1).

## The order, once the answers are in

1. A Dockerfile (Python 3.12 slim with uv, a non-root user, listening on
   $PORT), and a hosted start: the same app with Secure cookies, the allowed
   host from an environment variable, and local model providers off.
2. With 1A: the corpus in Postgres (a migration, a loader, the fetch writing to
   it), measured against today's loader so a match gives the same ranking.
3. The privacy page, with the two facts from Mahdi as settings.
4. Deploy to Cloud Run with the Neon URL and the secrets; walk the tester flow
   on the real address (an invite, the guide, a match, a mark, delete).
5. The nightly job (2A), and the quota on the Gemini key.
