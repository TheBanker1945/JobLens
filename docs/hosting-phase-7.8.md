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

- **7.8.2, the container**: `Dockerfile`, `.dockerignore`, `scripts/hosted.py`.
  Tried locally against Neon: a new tester's whole path works (guide, upload,
  a match of ten in 31 s, results, usage recorded), no CV vector reached the
  shared table, a foreign host is refused, and on a public host name the
  session cookie is Secure. The trial account was deleted afterwards.

- **7.8.3, the privacy page**: `/privacy` in five languages, written from what
  the code does, linked from the login page and settings. The two facts only
  Mahdi can give are settings: JOBLENS_OPERATOR (who runs it) and
  JOBLENS_CONTACT (how to reach them); the hosted server warns at start while
  they are missing. A hosted server now purges expired upload files, links and
  sessions by itself (at start, then every 6 hours), so "deleted after 30
  days" is true without anyone running `db.py purge-files`.

- **7.8.4, deployed (2026-09-29)**: https://joblens-883656455192.europe-west4.run.app,
  Cloud Run service `joblens` in europe-west4, running as the service account
  `joblens-run`, which may read three secrets and nothing else. The secrets
  (`joblens-database-url`, `gemini-api-key`, `joblens-secret-key`) are stored
  in europe-west4 only. `.gcloudignore` decides what leaves this machine for
  Cloud Build: 137 files (the Dockerfile, pyproject, the lockfile, src/ and
  scripts/hosted.py), checked with `gcloud meta list-files-for-upload` before
  the first deploy. A trial tester on the live address signed in over HTTPS
  (cookie Secure), uploaded, and ran a match that finished while nobody asked
  about it -- CPU always allocated works -- then was deleted.

- **7.8.5, the nightly update in the cloud** (Mahdi, 2026-09-29: nothing runs
  on his machine; JobSpy/Indeed stays, tried from the datacenter). Cloud Run
  job `joblens-nightly` (the Dockerfile's `nightly` stage, scripts/nightly.py),
  1 CPU, 2 GiB, up to 3 hours, never retried automatically, as service
  account `joblens-nightly` (its bucket, the database address and the Gemini
  key; not the encryption key). Its disk is the bucket
  `joblens-state-883656455192` in europe-west4 -- private, versioned, old
  versions kept 30 days -- holding only vacancy state (the allow-list in
  src/joblens/cloud/state.py), seeded once from Mahdi's laptop: 28 files, 23
  MB; his CV, labels and runs stayed home. Cloud Scheduler starts it at 03:00
  Amsterdam time as `joblens-scheduler`, which may start this job and nothing
  else. The live app reloads a new publish within 15 minutes.

**A run by hand**, e.g. while the switch is off, or to finish a night whose
fetch went fine (index and publish only):
`gcloud run jobs execute joblens-nightly --region europe-west4 --args=--force`
and `--args=--force,--no-fetch`. The script is the image's ENTRYPOINT, so
`--args` are appended to it; with a CMD they replaced it (the first recovery
attempt ran "--force" as a program).

**Rebuilding the nightly image** after a change to the fetch:
`gcloud builds submit --config cloudbuild.nightly.yaml`, then
`gcloud run jobs update joblens-nightly --region europe-west4 --image ...`
(the command is in cloudbuild.nightly.yaml).

**Redeploying** the app after a change: the same command, from a checkout of main.
The vacancies are refreshed by `daily_update.sh` on Mahdi's machine
(publishes to Neon); a server picks them up when it next starts, and Cloud
Run stops an idle server after a while, so the first visit of a day gets the
night's set.

## Deploying to Cloud Run (needs Mahdi's Google Cloud project)

What the container needs from Cloud Run, learned from the local trial:
- **CPU always allocated** (`--no-cpu-throttling`). A match runs in a thread
  after its request has answered; with Cloud Run's default the CPU goes when
  the answer is sent, and the match would stall until the next request.
- **At most one instance** (`--max-instances 1`). A starting server marks
  every open match "interrupted", which is right after a restart and wrong
  when a second instance starts beside a first that is still running one.
- **At least 1 GiB of memory**: the corpus and its vectors are held in memory.
- **The address is known before the first deploy**:
  `joblens-<project number>.europe-west4.run.app`. It goes in JOBLENS_HOSTS.

Once the project exists (billing on; the Run, Artifact Registry, Cloud Build
and Secret Manager APIs enabled), the secrets go in Secret Manager -- the
Neon address, the Gemini key, JOBLENS_SECRET_KEY -- and one command builds
and deploys (the values in capitals come from the project):

```bash
gcloud run deploy joblens --source . --region europe-west4 \
  --service-account joblens-run@PROJECT_ID.iam.gserviceaccount.com \
  --allow-unauthenticated --no-cpu-throttling --max-instances 1 --memory 1Gi \
  --set-env-vars JOBLENS_HOSTS=joblens-PROJECT_NUMBER.europe-west4.run.app \
  --set-env-vars CV_PROVIDER=gemini,CV_MODEL=gemini-3.8-flash,CV_THINKING=false \
  --set-env-vars EMBED_PROVIDER=gemini,EMBED_MODEL=gemini-embedding-2 \
  --set-env-vars CV_BASE_URL=https://generativelanguage.googleapis.com/v1beta/openai,EMBED_BASE_URL=https://generativelanguage.googleapis.com/v1beta/openai \
  --set-secrets DATABASE_URL=joblens-database-url:latest \
  --set-secrets CV_API_KEY=gemini-api-key:latest,EMBED_API_KEY=gemini-api-key:latest \
  --set-secrets JOBLENS_SECRET_KEY=joblens-secret-key:latest \
  --set-env-vars "JOBLENS_OPERATOR=YOUR NAME,JOBLENS_CONTACT=YOUR ADDRESS"
```

"--allow-unauthenticated" lets the internet reach the login page; JobLens
itself decides who is signed in. Invites are still made from a laptop, with
`JOBLENS_BASE_URL` set to the hosted address and `DATABASE_URL` to Neon.

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
