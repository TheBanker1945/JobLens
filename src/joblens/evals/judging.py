"""Does the judge put the vacancies a person would apply to above the others?

3.6 measured the judge by counting two mistakes -- `strong` on a "would not
apply", `weak` on a "would apply" -- and by that count it was nearly perfect on
four invented CVs. On the real CV, labelled by its owner, it called all five
"would apply" vacancies on the shortlist `weak`, made the same call four runs
in a row, and ordered the shortlist *worse* than retrieval had (nDCG 0.82 in
retrieval's order, 0.75 in the judge's). Nothing on the old screen could show
that, because a count of mistakes says nothing about order, and order is what
the judge is used for: `Judged.rank_key` sorts the list by it.

So two numbers, both about order:

- **concordance**: take every two vacancies the person graded differently.
  In what share does the judge put the better one higher? A tie counts half.
  0.5 is a coin, 1.0 is the person. Every pair counts, which is what makes it
  worth reading on a CV with 24 labels, and it needs no cut-off.
- **nDCG over the same vacancies in two orders**: as retrieval ranked them and
  as the judge ranked them. Retrieval chooses who gets a call and the judge
  chooses the order; this says whether the second choice helps. The ideal is
  the best order *of these vacancies*, not of every labelled one -- it scores
  the reordering, not whether retrieval found everything, which
  `evals/matching.py` already measures.

Neither is averaged across CVs, for the reason `evals/matching.py` gives: a CV
is a person, and there are five of them.
"""

import math
from collections.abc import Callable, Iterable


def concordance(scored: Iterable[tuple[float, int]]) -> float | None:
    """The share of differently-graded pairs that `score` puts in the right order.

    `scored` is (score, grade) per vacancy, higher meaning better on both. A
    pair with equal scores counts half: the judge did not choose. None when no
    two grades differ, because then there is no order to get right -- a CV
    whose labels are all "would not" (the control) has nothing to say here.
    """
    items = list(scored)
    right = total = 0.0
    for i, (score_a, grade_a) in enumerate(items):
        for score_b, grade_b in items[i + 1 :]:
            if grade_a == grade_b:
                continue
            total += 1
            if score_a == score_b:
                right += 0.5
            elif (score_a > score_b) == (grade_a > grade_b):
                right += 1
    return right / total if total else None


def reordered_ndcg(order: list[str], grade: Callable[[str], int]) -> float | None:
    """nDCG of `order` against the best order of the same keys.

    gain = 2^grade - 1, the same as the matching eval: "would apply" is worth
    three times "might". None when nothing in the list has a gain.
    """
    gains = [2 ** grade(key) - 1 for key in order]
    best = _discounted(sorted(gains, reverse=True))
    return _discounted(gains) / best if best else None


def _discounted(gains: list[float]) -> float:
    return sum(gain / math.log2(position + 1) for position, gain in enumerate(gains, 1))
