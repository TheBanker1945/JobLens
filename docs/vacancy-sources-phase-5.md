# Phase 5 — More Dutch vacancies, without getting blocked

The analysis that started this phase, and the decisions Mahdi made on it
(2026-09-22). It says **what** and **why**; the learning log says how each step
went.

## Where Dutch vacancies actually live

Almost every vacancy starts in an employer's own system, and most of those
publish public JSON meant to be read. The boards copy from there.

| layer | examples | text | tells us a job closed? | ban risk |
|---|---|---|---|---|
| 1. employer systems (ATS) | Recruitee, Greenhouse, SmartRecruiters, Workday, Lever, Ashby, Personio, Teamtailor | full | yes: gone from the board | none |
| 2. public sector | werkenbijdeoverheid.nl, EURES (carries werk.nl/UWV), AcademicTransfer | full | via sitemap or date | very low |
| 3. aggregators | Indeed, LinkedIn, Nationale Vacaturebank, Jobbird, Jooble | often a copy | no | medium to high |

So the aggregators are most useful as a **discovery layer**: Indeed's
`job_url_direct` and jobdataapi's `application_url` name the employer's own
board. Measured on the 258 aggregator vacancies stored on 2026-09-22, they point
at 25 Recruitee boards (3 were configured), 10 SmartRecruiters companies
(Coolblue, KPN, Boskalis, ...), 8 Workday tenants (ING, Alliander, Heijmans,
...), 10 Greenhouse short links and 9 Jobylon postings, plus 78 employer domains
on other systems. Read those boards directly: full text, closure for free, and
nothing to be blocked by.

## What was measured, per platform (2026-09-22)

**Use:** more Recruitee boards (custom domains work too:
`vacatures.coloriet.nl/api/offers/` answered with 44 offers), SmartRecruiters
(public Posting API), Greenhouse short links, werkenbijdeoverheid.nl (1,391
vacancies in a sitemap with `lastmod`, a superset of werkenvoornederland.nl's,
`Request-rate: 10/1`), EURES (6,270 Dutch hits for "developer", but 34 of 50
first-page records were German-language, so filter on language and date).

**Later, politely:** Workday (JSON behind the careers page, 20 per page, one
detail call per job), AcademicTransfer (694 vacancies, `Crawl-delay: 10`),
Jobbird (sitemaps with `lastmod`, rich JSON-LD, many stale entries).

**Skip:**
- Nationale Vacaturebank, Intermediair, Tweakers: every vacancy redirects to a
  DPG Media consent wall. DPG also owns AutoTrack, whose owner won *Innoweb v
  Wegener* (C-202/12) against a meta search engine.
- werkzoeken.nl, ictergezocht.nl: a Cloudflare challenge even on robots.txt.
- IamExpat, YoungCapital, Glassdoor: robots.txt disallows the job pages.
- Adzuna's API: registration, truncated descriptions, and terms that restrict
  aggregation.
- LinkedIn: robots.txt disallows `/jobs-guest/` for every crawler, Googlebot
  included.

## Decided (Mahdi, 2026-09-22)

1. **Scope: Zuid-Holland, Noord-Holland, Utrecht and Zeeland; software and AI
   engineering, and everything similar to that.** This is the cost gate: indexing
   costs about 0.27 cent per vacancy ($0.55 for 202 in 2.4), so only what falls
   inside the scope is extracted and embedded. It lives in config, so a fork with
   another region changes one table. It applies to new fetches only: the
   vacancies that 3.1–3.5's labels refer to stay in the store.
2. **The discovered board list is committed.** Company board slugs are public,
   and a fork should not have to rediscover them.
3. **LinkedIn stays off.**

## Constraints

- Nothing gets around a refusal: no proxies, no rotating addresses, no browser
  disguise, no CAPTCHA solving. When a site says no, the source stops and a
  person decides.
- Documented APIs are used as documented; robots.txt governs what is *crawled*.
- Scraping still never runs while someone is using JobLens.
- The scope is a corpus scope, not a preference. Phase 3 decided a preference
  only ever *moves* a vacancy. The scope decides which vacancies JobLens holds at
  all, and the run report counts what it left out.
- Evals get a frozen corpus snapshot before the corpus grows: 3.7 saw every
  labelled score move when it grew from 198 to 279.

## Milestones

| step | milestone | branch |
|---|---|---|
| 1 | one gate for every request: pacing, budgets, refusals remembered | `feat/5.1-polite-fetching` |
| 2 | discovery from aggregator links, more employer boards, the scope filter | `feat/5.2-discovery` |
| 3 | vacancies that closed: `last_seen`, closure from boards and sitemaps | `feat/5.3-closed-vacancies` |
| 4 | werkenbijdeoverheid.nl through its sitemap; robots.txt for crawled sources | `feat/5.4-government` |
| 5 | EURES, filtered on language and date | `feat/5.5-eures` |
| 6 | Workday, and a generic JSON-LD reader for employer sites | `feat/5.6-workday-jsonld` |
