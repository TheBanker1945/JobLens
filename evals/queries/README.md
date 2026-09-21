# Labelled queries for the retrieval eval

One file per corpus, read by `scripts/eval_retrieval.py --corpus <name>`:

- `samples.json` — the 10 fictional vacancies. Committed **and** reproducible:
  clone the repo and you get the same numbers. This is the public test.
- `raw.json` — the 202 real vacancies in `data/raw/`, which are never committed.
  Committed anyway, because it records decisions rather than data: a key like
  `greenhouse:7436701` is a public job-board id, not personal data. Nobody else
  can reproduce these numbers, and the learning log has to say so.
- `raw.draft.json` — the queries before anyone judged them. The draft decides
  which queries exist; editing a query's text there drops its old answers,
  because an edited query is a different question.

```json
{
  "split": "dev",
  "query": "elektromonteur met vca certificaat",
  "relevant": ["jobdataapi:11f2"],
  "judged": ["jobdataapi:11f2", "jobdataapi:8c04"],
  "note": "trade + certificate"
}
```

`relevant` is what counts as a correct answer. `judged` is every vacancy that was
shown, relevant or not, so a later round — a new embedding model, chunked
documents — only has to judge candidates that are genuinely new. Without it,
"not relevant" and "never looked at" are indistinguishable.

## Splits

- `dev` — used to choose a variant. Look at these mistakes freely.
- `holdout` — never used for choosing. Only for measuring before and after. A
  change that improves dev but not holdout was fitted to the dev queries.

## Where the candidates come from

`scripts/label_queries.py` **pools**: a candidate is any vacancy that any variant
in `evals/retrieval.toml` puts in its top N. Judging only what today's default
finds would rig the comparison — a variant that surfaces a good vacancy nobody
was asked about would be scored wrong for finding it.

Anything no variant retrieved counts as not relevant. That is the standard
compromise (reading all 202 per query is not realistic) and it means these
numbers compare the variants that were pooled from, not retrieval in the
abstract.

## Who labelled this

**`raw.json` was labelled by Claude, not by Mahdi** (2026-09-21, at Mahdi's
request). Every call below is Claude's reading of what a Dutch job seeker would
click, not Mahdi's. `raw-review.md` lists every decision so they can be checked
and overruled; `samples.json` is still Mahdi's own labelling from milestone 1.6.
Any claim resting on these numbers should say whose judgement it rests on.

## Labelling guidelines

These rules define what "relevant" means, and the eval means nothing without
them. Where a rule was decided during labelling, it says so.

**The test: would you click it?** A vacancy is relevant if someone who typed this
query would seriously consider applying. Not "does it share words".

**Place.** The query names a city → another city is not relevant. The query names
no city → place does not decide it.

**Seniority.** The query says nothing about level → any level is relevant. It says
"senior" or "junior" → the other end is not.

**Adjacent roles.** The same work under a different title is relevant
(Elektromonteur / Monteur Elektrisch). Related field, different work is not
(a lecturer *in* electrical engineering is not an electrician's job).

**Language.** The language a vacancy is written in does not decide relevance,
unless the query asks for it ("nederlands sprekend").

**Not a vacancy.** An open-application page ("Open sollicitatie", "Open
application") is never relevant to any query: there is no job behind it.

**Training routes.** A BBL track or learning route is relevant when the query is
about that trade (it *is* how you enter it), but an internship is not a "bijbaan":
a stage is structured study, a bijbaan is paid work beside it.

**A query with no relevant vacancy** gets removed from the draft. It scores 0 for
every variant and measures nothing. **So does a query where nearly everything is
relevant** — "baan waarbij ik veel met mensen werk" matched 40+ of 202 vacancies
and was dropped for that reason. A query has to be able to be wrong.

## Calls made while labelling (2026-09-21, Claude)

Review these first — they are where the ground truth could reasonably differ.

- **Hours are a preference, not a filter.** "thuiszorg parttime" keeps a 36-hour
  home-care job: the sector decides, and a seeker would click and check the hours.
- **A named tool is a filter.** "data engineer met databricks" has exactly **one**
  relevant vacancy, because only one data engineering job actually uses Databricks.
  The four Databricks *company* vacancies (sales, GTM, engineering management) are
  not relevant — the employer's name is not the skill.
- **"elektromonteur met vca"**: relevant = the ad lists elektrotechniek *and* VCA
  or NEN 3140. That pulls in camera and site-security fitters, whose work is
  electrical installation, and leaves out the purely mechanical monteurs.
- **GGZ means mental healthcare**, so somatic, district and hospital nursing are
  out even though they are all "verpleegkundige". 14 of the 202 qualify.
- **"geen diploma nodig" is structural, not textual.** *No vacancy in the corpus
  says it.* Relevant = no education level required, no years of experience
  required, and entry-level hands-on or service work. 10 vacancies.
- **"auto van de zaak"** was labelled from the text, not the pool: 17 vacancies
  mention a werkbus, bedrijfsauto or leaseauto, and all 17 are relevant even
  where no variant retrieved them.
- **Product roles count when the product is the thing asked about**, so a Group
  Product Manager for ML-driven fraud counts for "machine learning en ai", and a
  Product Owner whose tool list happens to include AI does not.
- **Open-application pages are never relevant** (the rule above), and were
  deliberately left in the corpus so the eval could measure what they cost. It is
  up to 22 points of hit@1 — see the 3.1 entry in the learning log.

## Known problems in the corpus that labelling exposed

- `recruitee:396939` and `recruitee:312680` are open-application pages, not jobs.
- `indeed:7e3b0e360662d3f5` and `indeed:17ae9cfdbd2ad40e` are the **same vacancy
  twice**, byte for byte. `Vacancy.fingerprint` missed it because one gives the
  city as "Utrecht" and the other as "UT".
- `indeed:685a77ed7614865d` and `indeed:ce81cd809343d07a` are one job advertised
  under two company names (BOSMAN and MediReva, who are the same business).
- `greenhouse:7457386` and `greenhouse:7541003` carry identical body text under
  different titles.

All four pairs are labelled on merit: if the job is relevant, both copies are.
