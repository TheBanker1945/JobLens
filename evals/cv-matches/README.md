# Judged CV-to-vacancy pairs

One file per CV, read by `scripts/eval_cv_matching.py`. Written by
`scripts/label_cv_matches.py`, which pools candidates from every variant in
`evals/cv-matching.toml` so no variant is scored wrong for surfacing a good
vacancy nobody was asked about.

```json
{
  "cv": "sanne_vermeulen",
  "corpus": "raw",
  "judged_by": "...",
  "relevant": ["jobdataapi:56848625"],
  "maybe": ["jobdataapi:56726958"],
  "judged": ["jobdataapi:56848625", "jobdataapi:56726958", "..."],
  "note": "..."
}
```

**Three answers, not two.** `relevant` is "would apply", `maybe` is "would
consider", `judged` is everything that was looked at. A job list is an ordering
rather than a set of right answers, so second best has to be worth something:
nDCG@10 scores an application at gain 3 and a maybe at 1.

## Who judged this

**Claude (Opus 5) judged all four files on 2026-09-21, at Mahdi's request, and
nobody has reviewed them.** Every call below is Claude's reading of what these
invented people would apply to. The CVs are invented too, so this measures
Claude's ranking against Claude's judgement of Claude's CVs — which is exactly
as circular as it sounds, and the reason the real test is Mahdi's own CV, judged
by Mahdi. Until that exists, treat these numbers as "the ordering is not
obviously broken", not as "matching works".

## The rules used

**The test: would this person actually apply?** Not "do the words overlap".

**A hard requirement the CV cannot meet caps a vacancy at `maybe`.** Sanne is an
mbo-4 verpleegkundige, so every hbo-V role (wijkverpleegkundige, SPV,
regieverpleegkundige) is a maybe however exactly the work fits — she would want
it and could not have it. Same for Lisa against a vacancy asking wo.

**A region stated on the CV is a filter, not a wish.** Youssef writes "regio
Tilburg of Eindhoven", so a warehouse job in Geleen or Amsterdam is a maybe and
never an apply. Where a vacancy names no city it is judged on the work alone.

**Hours are a filter when the CV states them and the vacancy contradicts them.**
Youssef asks for fulltime, so a 16-24 hour job is `no`, not `maybe`.

**A step sideways is `maybe`, a step down or a different profession is `no`.**
Sales, account management and teaching are out for all four, however much
vocabulary they share with a CV — Databricks' sales vacancies are full of the
words on Lisa's CV and she would not apply to one.

**A training route counts when it is how you enter the work** (BBL,
"verpleegkundige in opleiding tot", "oncologieverpleegkundige in opleiding"), and
an internship does not count for someone with eight years of experience.

## The control

`ingrid_solheim.json` has an empty `relevant` list on purpose: an Arctic marine
biologist against 198 Dutch vacancies has no right answer, and all 40 pooled
candidates were read to confirm it. She is left out of every ranking metric —
with nothing to find, every variant scores zero and that says nothing about any
of them. What she measures is the score a CV gets when the answer should be "no",
which is the only evidence a refusal threshold could be built from.

## What is missing

**Coverage of the corpus (measured 2026-09-22, milestone 3.7).** These labels
were pooled when the corpus was 198 vacancies. It is now 279, and the top ten no
longer sits inside what was judged:

| cv | share of the top 10 that carries a label |
|---|---|
| youssef_bakker | 100% |
| lisa_de_vries | 60-80% depending on the variant |
| sanne_vermeulen | 40-70% |

An unlabelled vacancy scores gain 0 exactly like one judged "would not apply", so
**every metric over Lisa and Sanne is now a floor** and a variant is punished for
surfacing something nobody read. `scripts/eval_cv_matching.py` prints the share
in a `labelled` column so this cannot be forgotten again. Youssef is the control
for the claim: his coverage is 100% and his five scores are unchanged from 3.5,
while both other CVs dropped across every variant.

**Lisa and Sanne need re-pooling** against the current corpus. Not done in 3.7,
because relabelling is an opinion and this file has to say whose.

**Mahdi's own CV.** These four are invented, and an invented CV is written by
someone who has already read the vacancies. The numbers per CV in the 3.5 entry
of the learning log are never pooled for that reason.
