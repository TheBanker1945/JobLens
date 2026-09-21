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

## Labelling guidelines

Proposed by Claude, to be confirmed or overruled by Mahdi while labelling. These
rules define what "relevant" means, and the eval means nothing without them.

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

**Training routes.** BBL tracks, traineeships and internships — decide once while
labelling and write it here.

**A query with no relevant vacancy** gets removed from the draft. It scores 0 for
every variant and measures nothing.

## Mahdi's calls

Filled in while labelling.
