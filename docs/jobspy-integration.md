I want to fully integrate job scraping into JobLens using JobSpy (https://github.com/speedyapply/JobSpy), so the app can pull real job listings from Indeed and LinkedIn and feed them into the existing AI matching that already lives in this codebase.

## The goal

A user (right now that's me, but JobLens is built to be shown to recruiters) uploads their CV and gets matched against real, fresh job listings, with the AI side of the app explaining why each job fits or doesn't. Scraping is the plumbing that feeds this; the matching experience is the product.

## How I want you to work

Before writing any code, explore the repo thoroughly and understand how the existing AI infrastructure works: how CVs are handled, how matching is done, where data is stored, and how the app is structured. Then come back to me with a plan for how scraping fits into what already exists. Reuse and extend what's there rather than building parallel systems. Wait for my go-ahead before implementing.

## What I know about JobSpy (context, not instructions)

I've looked into how JobSpy works internally. Take this into account when designing, but make your own decisions based on the codebase:

- Indeed is fetched through Indeed's mobile app API. One request returns about 100 jobs with full descriptions, so it's cheap and rarely rate-limited. The risk is breakage: if Indeed changes that API, it returns zero results with no obvious error.
- LinkedIn uses public guest endpoints (no login, so no account risk). Search pages are paced, but fetching full descriptions fires one request per job with no delay, and that's what gets an IP throttled.
- When LinkedIn throttles description requests, JobSpy fails silently: the job comes back with an empty description and no error. Jobs without descriptions must never reach the matcher as if they were valid.
- The `distance` parameter is in miles and defaults to 50, which is far too wide for the Netherlands.
- Some options can't be combined on Indeed (e.g. recency filter vs. job type/remote filter), so some filtering has to happen on our side.
- JobSpy's `user_agent` parameter is accepted but ignored by the Indeed and LinkedIn scrapers.

## What "done" looks like

- Fresh jobs from Indeed and LinkedIn land in JobLens automatically on a schedule, deduplicated, without me having to run anything by hand.
- Scraping never happens live while someone is using the app. A demo in front of a recruiter must never depend on a job board responding at that moment.
- The scraper is a replaceable source behind a clean boundary, so another job source could be added or swapped later without touching the matching logic.
- Request volume stays modest and paced, so I don't get my IP throttled at personal/showcase scale.
- Failures are visible: if a source breaks, gets throttled, or returns suspiciously empty data, I find out, instead of the app quietly matching against bad data.
- The code is clean and readable enough to stand as part of a public portfolio project that recruiters and engineers will look through.

If anything in the existing codebase conflicts with these goals, or you see a better approach than what I'm implying, tell me before building.
