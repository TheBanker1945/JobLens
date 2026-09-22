# Phase 3 — A developer-facing UI, and the data work it needs

Mahdi's brief, written before anything was built, kept here the way the phase-2
brief is. It says **what** and **why**, not how.

## Why this, now

I labelled my own CV in 3.7.2 and the judge disagreed with me five times out of
ten. Three "weak" verdicts were jobs I would apply to, all rejected on years of
experience or hbo/wo education, and one "strong" (84) I would not apply to. My
y/n answers carry no reason, so nobody can tell whether the system or I was
wrong. That is the problem to solve.

This is for ME to test with first: I give my CV and my preferences, we re-run the
matching, and I review what came back AND what did not. Longer term this becomes
multi-user and hosted, so nothing should assume one laptop, but we are not
building auth, uploads or a database yet.

## The steps, smallest first

1. **The full ranking, not just the judged rows.** Today a run stores the ~12
   vacancies it judged. The other 267 were rejected by retrieval with no score,
   no record and no reason, and that is the rejection I cannot review. Store the
   full ranking with each vacancy's score and whether it was judged. Everything
   below depends on this.

2. **A storage seam.** One module that all reads and writes of runs, labels and
   preferences go through, file-backed for now. No database yet: the preferences
   schema does not exist, and a DB earns its place at multi-user or hosting. The
   seam is so that swapping to Firestore later is a day, not a week.

3. **A read-only viewer** over a stored run: recommended, judged-and-rejected,
   and never-shortlisted, with the vacancy text and the extracted fields one
   click away. This is where I will see whether a rejection was right. Also let
   me browse the corpus itself with extracted fields beside the raw text.

4. **Marking and reasons in the viewer.** When I mark a vacancy as wrongly
   rejected or wrongly recommended, capture a one-line reason with it and write
   it to the labels the evals already read. This replaces the CLI y/m/n, which is
   exactly what produced unusable labels. Never infer my rule from a pattern of
   answers and write it down as if I had stated it.

5. **Preferences**, after I have twenty real reasons to build the profile from
   rather than guessing at a form. Keep them separate from the CV: a CV is facts
   about my past, preferences are constraints on my future. Decided already: a
   preference only ever MOVES a vacancy, never deletes it, and the UI says when a
   preference cost a job places. Judge-time stretch rules ("they ask 4 years, I
   apply anyway") matter more than filters and are what would have fixed three of
   my five disagreements.

## Constraints

- CLAUDE.md's rule holds: no framework until we have built the thing by hand
  once. The smallest thing that serves a local page, and a justification for any
  new dependency.
- The API layer stays local-only for now, but treat it as the thing hosting will
  wrap later. Any provider's API key must keep working, including a local model.
- Evals still decide: no claim that matching improved without a number, and my
  existing labels are the "before".
- My CV, the real vacancies and my labels stay out of git.

## Milestones

| step | milestone | branch |
|---|---|---|
| 1 | the full ranking, stored | `feat/4.1-full-ranking` |
| 2 | the storage seam | `feat/4.2-storage-seam` |
| 3 | the read-only viewer | `feat/4.3-viewer` |
| 4 | marking and reasons | `feat/4.4-marking-and-reasons` |
| 5 | preferences | later, after twenty reasons |
