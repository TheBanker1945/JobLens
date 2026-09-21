# Sample CVs

Three invented people, so that someone who clones JobLens can run CV matching
end to end without owning a CV — and so that the tests have something to read.

**These were written by Claude on 2026-09-21, at Mahdi's request.** Every name,
address, phone number, e-mail and employment date in them is made up. The
`example.com` and `example.org` addresses are reserved by RFC 2606 and reach
nobody. Mahdi's own CV is never in this repository: it lives in `data/raw/cv/`,
which git ignores.

| File | Who | What it is for |
|---|---|---|
| `lisa_de_vries.md` | data analyst, Amsterdam, 3 years | the ordinary case: a CV that should match the tech end of the corpus |
| `lisa_de_vries.pdf` | the same CV as a PDF | so the PDF reader is exercised by anyone, not only on a machine that has a real CV on it |
| `youssef_bakker.md` | warehouse voorman, Tilburg, 8 years | a second sector, to show the top of the list actually changes with the CV |
| `ingrid_solheim.md` | Arctic marine biologist, Tromsø | fits **nothing** in a Dutch job corpus, on purpose: the case where the honest answer is "no" |

## What they can and cannot measure

They keep the repository runnable. They do **not** settle whether matching is any
good, and the phase-1 lesson says why: conclusions drawn on ten invented
vacancies all reversed on the real corpus. An invented CV has the same defect in
the same direction — it was written by someone who had already read the
vacancies, so its vocabulary lines up with theirs more neatly than a real CV's
does, and it is clean where a real CV is messy.

So eval numbers are always reported **per CV, never pooled**, and a conclusion
that only holds on these three is not a conclusion.

## The PDF

`lisa_de_vries.pdf` was produced by `scripts/make_sample_pdf.py`, a deliberately
dumb one-column PDF writer built to avoid a dependency. Rebuild it with:

```
uv run python scripts/make_sample_pdf.py data/samples/cvs/lisa_de_vries.md
```

A CV exported from Word or Canva is laid out very differently. This one proves
the text layer is found, read and redacted; it does not prove anything about
two-column layouts.

## What redaction does to them

`uv run python scripts/read_cv.py data/samples/cvs/lisa_de_vries.md --show-sent`
prints what was removed and the exact text that would leave the machine.

The patterns are Dutch: a Dutch postcode, Dutch street suffixes, Dutch and
international phone shapes. `ingrid_solheim.md` is where that stops working — her
e-mail, phone and date of birth are removed, but "Storgata 71, 9008 Tromsø" is
not a shape this code knows. That is left visible rather than patched over: the
limit is real, and the removal list is how you find it on your own CV before you
send it anywhere.
