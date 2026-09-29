# Phase 7 — The web app: from scripts to something people log in to

Mahdi's brief (2026-09-25), in his words: a web application "on which users and
me can upload their CV, answer questions about the job that they are looking
for, contract type etc, and eventually be able to get vacancy matches based on
the user data and the actual vacancies", which "will eventually be hosted", where
"the user also will be able to link their OWN ai via API". The frontend "needs to
be in my style": style questions and mockups first, he chooses, then it is built.

## Decisions (Mahdi, 2026-09-25)

Asked as eight questions, answered in one message. Recorded as given.

| # | question | answer |
|---|---|---|
| 1 | who is it for at launch | "mainly for me and a few testers" |
| 2 | who pays without a key | "a kind of freemium plan that only the testers get which will be limited so that i don't get a big bill from google or they can use their own key for as much usage as they want" |
| 3 | database | "an alternative free database such as firebase or anything else that matches perfectly so no supabase" |
| 4 | questionnaire v1 | the proposed list: contract type, hours, work mode, region and travel distance, minimum salary, seniority, languages, sectors or employers to avoid, and whether to apply when more years or a higher degree is asked |
| 5 | preferences before twenty labelled reasons | "sure" -- stated answers are the user saying the rule, which is allowed; inferring one is not |
| 6 | the uploaded file | keep the original 30 days so reader fixes can re-read it; "delete my data" removes everything at once |
| 7 | CVs per user | "one active plus its history" |
| 8 | language | "check their country of origin and make that the language that they will see, and also have the option to switch languages. English, dutch, german, french and spanish" |

## What the codebase already gave, and what it did not (2026-09-25)

- **Bring your own key is mostly there.** Every model call takes an
  `LLMSettings`, and only `scripts/` read `.env`. A user's key is a different
  `LLMSettings`, built from their account.
- **The storage seam** (`joblens.storage`, 4.2) exists so that a database is a
  new implementation, not a hunt through the repo. It has no owner yet, on
  purpose.
- **Missing:** a service layer (7.1, done), users, a preferences schema, a
  database, auth, uploads, background jobs, hosting.

## Four facts that shape the design

1. **Embeddings cannot be the user's key.** Every vacancy is embedded by
   gemini-embedding-2; a CV embedded by any other model cannot be compared with
   them. A user's key pays for reading the CV and judging (about $0.03-0.10 a
   run); the one embedding call per CV stays the operator's.
2. **A hosted server cannot reach a user's own Ollama** -- their localhost is not
   the server's. Local models stay a first-class option for anyone who runs
   JobLens themselves, and the hosted settings page has to say so.
3. **Most vacancies do not state what a questionnaire asks.** Of 1,425 extracted
   vacancies, 64% have no contract type, 53% no hours, 57% no salary, 34% no work
   mode. A preference therefore moves a vacancy and never removes it, and
   "unknown" costs nothing (the phase-3 rule, now with a number behind it).
4. **A judged run takes 21-64 s** (the last eight stored runs). A web request
   cannot wait that long, so a run is a background job the page watches.

On question 8: a country is not a language (an expat in Utrecht, a Belgian with
a French browser). The proposal for 7.7 is the browser's own language list
first, the country as a fallback, the switch always visible, and the choice
remembered. The judge's own sentences (summary, gap names) would follow the
chosen language -- a prompt change, so a PROMPT_VERSION bump and an eval -- while
quotes stay in the language of the CV or vacancy they are checked against.

## The database, researched 2026-09-25

A research pass over current free tiers (sources in the session record; the
numbers are the providers' own pages on that date and change often):

- **Neon (Postgres), Frankfurt** -- 0.5 GB and 100 compute-hours a month free,
  no card, scales to zero and wakes in a few hundred ms. pgvector's `halfvec`
  indexes up to 4,000 dimensions, so the 3,072-dim vectors fit. Users, CVs,
  runs, labels and usage are relational; the loose parts (profile, run, answers)
  go in JSONB. Locally it is any Postgres in Docker. Caveats: the 0.5 GB cap
  (the whole app is an estimated 150 MB), and free projects idle for 90+ days
  are "subject to deletion as of October 5, 2026".
- **Firebase (Firestore + Auth + Storage)** -- 1 GiB, 50k reads and 20k writes a
  day, the best local emulators, no idle pause. Against it: vector fields stop at
  2,048 dimensions; Firebase Auth processes data only in the US; Cloud Storage
  needs the paid Blaze plan since 2026-02-03 and its free quota covers US
  buckets only; loading 1,500 vacancies and 4,000 vectors on every cold start
  would eat the daily read quota in about nine starts.
- **Out:** Appwrite Cloud (pauses after 7 days without console activity),
  CockroachDB (free plan closed to new clusters from 2026-09-15), Xata and Prisma
  Postgres (trial / evaluation only), Aiven (no region guarantee), PocketBase
  (needs a disk Cloud Run does not have), Cloudflare D1 (built for Workers).

The recommendation was Neon, with CV files in an EU Cloud Storage bucket that
deletes them after 30 days. **Decided 2026-09-25: Mahdi went with the
recommendations.** One change made while building 7.2: the uploaded files sit in
a Postgres table (`cv_files`) with an expiry instead of a bucket. At tester
scale (a few MB) they fit Neon's 0.5 GB easily, and it is one service fewer to
set up, pay for and secure; a bucket stays possible in 7.8 if volume ever asks
for it.

## Milestones

| step | milestone | branch |
|---|---|---|
| 1 | matching as a call: `joblens.service`, uploads as bytes, errors as classes | `feat/7.1-service-layer` |
| 2 | users, CVs and the database behind the storage seam | `feat/7.2-database` |
| 3 | preferences v1: the questionnaire, re-ranking, stated stretch rules for the judge, measured against labels | `feat/7.3-preferences` |
| 4 | the API: FastAPI in place of the hand-built transport; upload, preferences, start a run, progress | `feat/7.4-api` |
| 5 | accounts: login, and every query scoped to its user | `feat/7.5-accounts` |
| 6 | bring your own AI: encrypted keys, test connection, measured models, the tester freemium quota | `feat/7.6-byo-ai` |
| 7 | the user-facing UI: style questions, 2-3 mockups, Mahdi picks, then build; five languages | `feat/7.7-ui` |
| 8 | hosting: container, hosted database and storage, secrets, scheduled fetching, privacy notice, delete-my-data, cost caps | `feat/7.8-hosting` |

From 7.2 on everything is designed as if there is no persistent disk, which
keeps every host open.

## Login (7.5)

Decided while building, within "go with your recommendations": invite-only
login links. The owner runs `scripts/db.py invite someone@example.com`, sends
the printed link however they like, and the link (once, within 7 days) starts a
30-day session. No passwords, no mail service, no Google project needed for the
tester phase, and nobody can sign themselves up. Google sign-in stays possible
in 7.8 as a second way to start the same sessions; it needs an OAuth client
created in Google Cloud.

## Bring your own AI and the tester allowance (7.6)

Built to answer 2 above: testers get a monthly allowance on the operator key
($1 each, $10 for all together, both in .env), or bring their own key for
unlimited use. For hosting (7.8), also cap the Gemini key itself in Google
Cloud (a requests-per-day quota): a budget alert there warns and never stops
spending, and JobLens's own limit is only as good as JobLens's code.

## The UI (7.7)

Mahdi's style answers (2026-09-26): Indeed, AIApply and LinkedIn as references;
professional, modern, minimalist; blue gradient and white; minimalist but not
too empty; a guide people can skip, landing on the dashboard; the preferences
as one form; desktop first but phone friendly; informal ("je"). Three
directions were drawn on a canvas; he chose **A "Helder"**: white, a top bar,
the gradient in one welcome band and on the main buttons, cards with the CV
quote behind each match, Plus Jakarta Sans.

Built in four steps: (1) the app frame, login page, dashboard, Dutch and
English; (2) the guide, the preferences form, My CV; (3) the matches page with
evidence and marking with a reason; (4) settings (own AI, spending, language,
download and delete my data), German, French and Spanish, phone polish.

Steps 1 and 2 are built (learning log 7.7.1, 7.7.2). The guide is shown to an
account until it is finished or skipped (`users.onboarded_at`, migration 0005)
or while it has no CV; accounts that already had a CV never see it. On a phone
the top menu becomes a bottom bar.

## Open questions from 7.3 (for Mahdi)

- **Hard lines or margins?** A vacancy 46 km away against a 40 km preference now
  sits behind every vacancy that contradicts nothing. A margin, or marking each
  answer "must" or "nice to have", would soften that. Decide after using it.
- **Is a year's contract "met uitzicht op vast" temporary?** Extraction says
  temporary (249 such vacancies against 181 permanent). A separate answer --
  "I accept a first-year contract with a view to permanent" -- would say what
  people mean.
- **Measuring it.** Answer `scripts/preferences.py ask`, then compare
  `eval_judge.py --labelled --cv mohammed --preferences` with the plain run.

