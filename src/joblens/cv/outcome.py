"""What the app says when your CV fits nothing. Built on verdicts, not on scores.

The brief's fourth question: silence, a bad list, or saying so. Saying so -- and
the interesting part is which signal is allowed to decide it.

**Not the cosine.** 3.5 measured the control CV (Ingrid Solheim, an Arctic marine
biologist against a Dutch corpus) at a top score of 0.646 where a CV that does fit
scores 0.766. Twelve hundredths of separation, measured on one control CV, is not
a threshold; it is a coincidence waiting to be shipped. Retrieval handed her a
confident number one under every variant.

**The verdict.** 3.6 measured the same CV through the judge: 0 strong, 0 possible,
10 weak, highest fit 5. The judge reads the vacancy and says no, and it says no
with a quote from the vacancy attached.

So the rule is counted off the verdicts, and it has two bands rather than one,
because one band gets it wrong on real data:

    cv                strong  possible  weak   ->
    ingrid_solheim         0         0    10   nothing fits
    lisa_de_vries          0         2     6   nothing is a clear fit
    sanne_vermeulen        4         ...        ordinary answer

A rule of "no strong means refuse" would have refused Lisa, whose labels name four
vacancies she would apply to. So `NOTHING` needs no strong *and* no possible, and
the middle band exists to say the weaker thing plainly instead of overstating it.

What a refusal is **not** allowed to be: silence, or a shorter list. The banner
goes above the list and the list still prints, with the reason each vacancy fails
and the gap summary underneath it. The only thing that changes is that the app
stops pretending.
"""

from dataclasses import dataclass
from enum import StrEnum

from joblens.cv.judge import Judged, Verdict


class Fit(StrEnum):
    NOTHING = "nothing"  # no strong, no possible: every shortlisted vacancy is a no
    NO_CLEAR_FIT = "no_clear_fit"  # something is worth a look, nothing is a yes
    OK = "ok"  # at least one serious candidate


@dataclass(frozen=True)
class Outcome:
    fit: Fit
    counts: dict[Verdict, int]
    best_fit: int
    judged: int
    corpus: int  # how many vacancies the shortlist was chosen out of
    corpus_name: str

    @property
    def refused(self) -> bool:
        return self.fit is Fit.NOTHING

    def headline(self) -> str:
        """One sentence, and it must not claim more than was measured.

        The app judged a shortlist, not the corpus. "Nothing in the corpus fits
        you" is a statement about 279 vacancies of which it read ten, so the
        sentence says what it actually checked.
        """
        if not self.judged:
            # Every call failed, which is not an answer about the CV at all.
            return "Nothing was judged, so this run says nothing about your CV."
        if self.fit is Fit.NOTHING:
            return (
                f"Nothing here fits you. The {self.judged} closest of "
                f"{self.corpus} {self.corpus_name} vacancies were all judged weak "
                f"-- the best of them scored {self.best_fit} out of 100."
            )
        if self.fit is Fit.NO_CLEAR_FIT:
            possible = self.counts[Verdict.POSSIBLE]
            return (
                f"Nothing here is a clear fit. Of the {self.judged} closest "
                f"vacancies, none is a strong match and {possible} "
                f"{'is' if possible == 1 else 'are'} worth a look with something "
                f"real missing."
            )
        strong = self.counts[Verdict.STRONG]
        return (
            f"{strong} of {self.judged} judged vacancies "
            f"{'is' if strong == 1 else 'are'} a strong match."
        )

    def advice(self) -> list[str]:
        """What to do about it, and only what follows from what was judged."""
        if not self.judged:
            return []
        if self.fit is Fit.NOTHING:
            return [
                "They are still listed below, with the reason each one fails and "
                "the requirements that keep coming up.",
                "This says the closest vacancies in this corpus are not yours; it "
                "does not say no such job exists. A wider fetch, a different "
                "search region, or a larger --top are the three things that change "
                "the answer.",
            ]
        if self.fit is Fit.NO_CLEAR_FIT:
            return [
                "A list where everything scores well tells you nothing, so this "
                "one does not. Read the gaps before the titles."
            ]
        return []


def assess(judged: list[Judged], *, corpus: int, corpus_name: str = "") -> Outcome:
    """Which of the three answers this run is. Counting, and nothing else."""
    counts = {verdict: 0 for verdict in Verdict}
    for one in judged:
        counts[one.judgement.verdict] += 1
    if counts[Verdict.STRONG]:
        fit = Fit.OK
    elif counts[Verdict.POSSIBLE]:
        fit = Fit.NO_CLEAR_FIT
    else:
        fit = Fit.NOTHING
    return Outcome(
        fit=fit,
        counts=counts,
        best_fit=max((one.judgement.fit for one in judged), default=0),
        judged=len(judged),
        corpus=corpus,
        corpus_name=corpus_name,
    )
