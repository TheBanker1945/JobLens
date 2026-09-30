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
| 9 | onboarding and invites: invite from the site, one question at a time | `feat/7.9.1-invites`, `feat/7.9.2-questions`, `feat/7.9.3-flow` |
| 10 | the owner's admin page: sources and their numbers, runs, run now, a schedule, testers' spend | `feat/7.10.1-admin-sources` and three after it |

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

All four steps are built (learning log 7.7.1-7.7.4). The German, French and
Spanish texts were written by Claude and still need a native speaker's read
before testers who speak them arrive. The guide is shown to an
account until it is finished or skipped (`users.onboarded_at`, migration 0005)
or while it has no CV; accounts that already had a CV never see it. On a phone
the top menu becomes a bottom bar. The matches page shows every vacancy a
match read and those the answers moved out of the shortlist; a mark there is
the same `Decision` the viewer (4.4) writes, with a required reason, so the
evals read a tester's marks the way they read Mahdi's.

## Onboarding and invites (7.9)

Mahdi's brief (2026-09-30), in his words: invite links he can make "through my
own website"; then an onboarding that starts with "uploading the CV, asking
questions one by one just like how AIapply is doing", "but with a twist";
"a single page with a single question at a time and a list to chose from, same
design choises we currently have, and with a bar above the question that fills
up the more questions you anqser", with the option "to skip a question"; and
the answers still to be given or changed "through the dashboard itself".

**What AIApply does**, read on 2026-09-30 from the strings its public
/product page ships (no account was made): a flow that asks about the person
first (work status, how they search, for how long), then the CV, which it says
"auto-fills" what follows (roles, level, salary, type of work, remote or
on-site, cities, companies to avoid), then a summary to approve. About ten
screens in between are sales copy ("the job market got brutal"). The look: a
bar split into one segment per phase, one big question, answers as large
tappable cards (a checkbox when several may be picked), and a bar at the bottom
with Skip and Continue.

A clickable mockup in style A was shown (claude.ai artifact, private), with
three candidate twists to switch on and off. Mahdi's answers, 2026-09-30:

| question | answer |
|---|---|
| which twist | **"What this changes"** only: one line per question saying how the answer moves the matches, or that it does not yet. Not the counts from our own vacancies, not hints from the CV. |
| work status and "what are you looking for" | **saved only**: stored and changeable, said to be unused, left out of a run's stamp; measured before they may act |
| the answers to "what are you looking for" | AIApply's seven (his pasted list did not arrive) |
| order | invites first (7.9.1), then the questions (7.9.2), then the flow (7.9.3) |

"Type of work" (AIApply: full-time, part-time, contract, internship) is asked
as two questions, contract and hours, because the preferences and the Dutch
vacancies keep them apart: full-time is a number of hours, not a contract. The
one-page form stays, for changing answers; the flow is for the first visit and
for the questions left open.

**7.9.1, built.** Settings -> For you, the owner -> Invite someone: an e-mail,
optionally a name and a language, and a link shown once (only its hash is
kept), with Copy. Below it every account, newest first: signed in (a session
that still works), a link waiting (until when), or needs a new link, with a
"New link" button. The server answers with the path (`/login#...`) and the page
puts its own address in front: behind Cloud Run's proxy the server is not sure
of its own. A new link replaces one not yet used (`Database.invite`, also what
`db.py invite` and `login-link` do now), so a link sent to the wrong chat dies
when its replacement is made. A page can only make testers; an owner is still
made with `db.py set-role`, and an invite asking for a role is refused.

**7.9.2, built.** Two answers join the preferences: `work_status` (employed,
unemployed, self-employed, student) and `goals` (AIApply's seven). They are
**saved only**, as decided: no rule reads them, the judge's prompt is
unchanged with or without them, and they are left out of `is_empty` and
`stamp`, so answers given before keep their digest (a test pins one computed
with the old code). The one-page form asks them last, under a note that they do
not move matches yet; there the goals question reads "Why are you looking?",
because the page itself is titled "What are you looking for?". A later
measurement decides whether they may act, as for any rule.

**7.9.3, built.** `/guide` is now the flow: the CV, then fifteen questions
one at a time (`ui/assets/questions.js`, one table), each a list of large
choices, a bar above that fills with the position and counts what was skipped,
and Back, Skip and Continue in a bar at the bottom. A single choice picked with
a click moves on by itself; with the keyboard it waits for Continue, or the
arrow keys would jump ahead. Each answer is saved on Continue by putting the
whole preferences document, the way the form saves it, so nothing new on the
server. Skipping never erases an earlier answer. A distance is passed by when
there is no home to measure from. The end is a summary with Change or Answer on
every row, then the first match. The dashboard's "What you're looking for"
card says how many questions are answered and links to `/guide?open`, the same
flow with only the open ones. "Type of work" is the contract and the hours
questions; the hours choices (full-time 36-40, four days 32-35, part-time up to
28) become one range. Sectors to skip are kept in the words chosen, in the
person's language, because the judge reads them as written.

## The owner's admin page (7.10)

Mahdi's brief (2026-09-30): "an interface within the website and with the
same design style on which i can see the scraper sources, the amount of
vacancies fetched per source and for joblens a subset of sources and numbers
per subset", a button to scrape by hand, and a way to set when it fetches by
itself. His answers to the questions that followed:

| question | answer |
|---|---|
| "a subset of sources and numbers per subset" | **both**: per source the funnel (listed last fetch, new, stored, open, in JobLens), and each source opens into its boards or searches with the same numbers |
| where the schedule lives | **in JobLens** (app_settings); Cloud Scheduler becomes an hourly wake-up and the job leaves at once unless it is due |
| the schedule form | **weekdays and up to two hours a day**; no cron text |
| extras | all four: run history and the sites refusing us (shown only), Stop and "fetch only this source", testers' spend, and the owner's things moved from Settings to the new page |

Rejected for the schedule: the page editing Cloud Scheduler itself. Cloud
Scheduler has no per-job permissions, so the app's service account -- which
reads three secrets and nothing else -- would need the right to edit every
scheduler job in the project and to act as `joblens-scheduler`. The price of
the choice made: about 23 starts a day that find nothing due and stop within
seconds, each waking Neon briefly. Not offered on purpose: a button that clears
a site's refusal (CLAUDE.md: nothing goes around a refusal), editing the scope's
word lists from the page (a change there needs a measurement), and more than
two fetches a day (each asks every site again).

Four steps, each its own PR: 7.10.1 the page, sources, runs and refusals;
7.10.2 Run now, Stop and one source (the app starts the Cloud Run job, with
`roles/run.jobsExecutorWithOverrides` on that one job only: run, run with
arguments, cancel); 7.10.3 the schedule; 7.10.4 testers' spend.

**7.10.1, built.** `/admin`, for the owner only (anyone else is sent to the
dashboard, and the menu shows it to the owner alone). The numbers come from
the nightly job, not from the web app: the app cannot read the job's bucket,
so at the end of every run the job counts the sources from its files
(`sources/overview.py`) and stores that with the run in `nightly_runs`
(migration 0008), which also holds when it ran, who started it, each step's
outcome, its new vacancies and the problems its fetch report named. A board's
vacancies are counted by the board that last listed them (sightings); a
search has only its last run, because a vacancy two searches found belongs to
neither. "In JobLens" is counted by the same `load_corpus` call that
publishing uses, so it is what a match ranks. The nightly switch and invites
moved here from Settings, unchanged.

Found on the way: the one-run-at-a-time lock (7.8.5) sat on a connection that
says nothing while the fetch runs, and Neon cut a connection idle for 7
minutes (measured 2026-09-30, "terminating connection due to administrator
command") -- taking the lock with it, and making the unlock at the end fail.
The lock's connection now asks `SELECT 1` every minute from a thread, and an
unlock on a lost connection is let go.

**7.10.2, built.** On the admin page: "Fetch now", "Only index and
publish", "Fetch only <source>" in every opened source, and "Stop this run"
while one is going. The app starts `joblens-nightly` through the Cloud Run
Admin API (`cloud/jobs.py`, httpx and the metadata server's token, as the
bucket is read) with `--force --by page` and `--no-fetch` or `--source X`:
forced, because the owner asked, whatever the nightly switch says. A run takes
about a minute to begin, so the app notes the request (`nightly_requested`)
and the page says "starting" until the run writes its own row; after 10
minutes without one, the button works again. One run at a time: the app
refuses a start while a run is going or starting (and the job's lock would
refuse a second anyway). Stop cancels the execution by the name the job wrote
down for itself (`CLOUD_RUN_EXECUTION`, migration 0009); the job writes its
bucket only at the very end, so a stopped run leaves nothing half-written and
keeps nothing it fetched, and the page lists it as "stopped before it
finished". Where no job is configured (`JOBLENS_NIGHTLY_JOB` unset, as on a
laptop) the page says runs start from the hosted JobLens.

To switch it on (needs Mahdi's go-ahead, nothing here does it by itself):

```
gcloud run jobs add-iam-policy-binding joblens-nightly --region europe-west4 \
  --member serviceAccount:joblens-run@gen-lang-client-0860584471.iam.gserviceaccount.com \
  --role roles/run.jobsExecutorWithOverrides
gcloud run services update joblens --region europe-west4 \
  --update-env-vars JOBLENS_NIGHTLY_JOB=projects/gen-lang-client-0860584471/locations/europe-west4/jobs/joblens-nightly
```

**7.10.3, built.** The switch's card is now "Automatic vacancy update": the
switch, and under it the days (Monday to Sunday) and one or two hours, Dutch
time, with "Next run: Wed 30 Sep, 18:00". The schedule is a pydantic model
(`cloud/schedule.py`) kept in `app_settings` ('nightly_schedule'); missing,
it is every day at 03:00 -- what Cloud Scheduler did before. Two hours must be
at least 6 apart, round the clock (22:00 and 02:00 are 4): every run asks
every site again. Cloud Scheduler becomes an hourly wake-up; `nightly.py`'s
`should_run` lets a start through only with `--force`, or with the switch on,
in a scheduled hour (Dutch time, so the clock change needs nothing), and when
no run began in that hour yet (Cloud Scheduler once delivered a start twice).
A start that finds nothing due leaves before touching the bucket.

**The order matters when this goes live.** Cloud Scheduler may start the job
every hour only once the job's image knows the schedule: the image running
now would fetch every hour while the switch is on. So: rebuild and update the
job, deploy the app, and only then

```
gcloud scheduler jobs update http joblens-nightly --location europe-west4 \
  --schedule "0 * * * *"
```

**7.10.4, built.** "What it cost this month": the total on JobLens's key
against the cap for all testers (JOBLENS_OPERATOR_MONTHLY_USD), and every
account with what it spent on JobLens's key against a tester's allowance
(JOBLENS_TESTER_MONTHLY_USD), on its own key, and how many paid calls --
from the `usage` table, the same month `spent_this_month` counts. Nothing new
is decided here; the page shows the numbers the checks already use.

**For Mahdi (found while building 7.10.4, not changed):** the cap for "all
testers together" (`budget.check`) counts everything on JobLens's key this
month, the owner's own use included. So the owner's matches use up the
testers' $10. The page shows the owner's part separately. Should the cap
count testers only? A one-line change in `check` and `spending` if so.

## Open questions from 7.3 (for Mahdi)

- **Hard lines or margins?** A vacancy 46 km away against a 40 km preference now
  sits behind every vacancy that contradicts nothing. A margin, or marking each
  answer "must" or "nice to have", would soften that. Decide after using it.
  The 7.7.3 walk put numbers on it: with a 40 km limit from Den Haag, all ten
  vacancies moved out of the shortlist were in Amsterdam, 53-55 km away.
  **Decided 2026-09-29 (Mahdi): "it can have a margin."** Built as p2: a
  quarter more, at least 5 km (rerank.allowed_km), said under the form field.
  The other answers keep no margin: a contract type is not "a bit" wrong.
- **Is a year's contract "met uitzicht op vast" temporary?** Extraction says
  temporary (249 such vacancies against 181 permanent). A separate answer --
  "I accept a first-year contract with a view to permanent" -- would say what
  people mean.
- **Measuring it.** Answer `scripts/preferences.py ask`, then compare
  `eval_judge.py --labelled --cv mohammed --preferences` with the plain run.

