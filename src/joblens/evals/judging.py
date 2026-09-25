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

import hashlib
import math
from collections.abc import Callable, Iterable
from dataclasses import dataclass

from joblens.cv.judge import (
    PROMPT_VERSION,
    Judged,
    MatchJudgement,
    judge_pairs,
    shown_vacancy,
    verify,
)
from joblens.cv.match import CVMatch
from joblens.cv.requirements import (
    ANSWERS_VERSION,
    REQUIREMENTS_VERSION,
    RequirementBook,
    RequirementJudgement,
    combine,
    judge_by_requirements,
)
from joblens.cv.store import CVCache
from joblens.llm.structured import Mode
from joblens.llm.types import ChatClient


@dataclass(frozen=True)
class JudgeVariant:
    """How a judgement was asked for, beyond the model and the prompt version.

    Every one of these changes the answer, so every one is part of the name the
    answer is stored under -- otherwise a run with thinking on would be handed
    the answers stored without it, and report them as its own. The default
    variant keeps the name 3.6 used, so nothing already paid for is lost.

    `sample` asks the same question again as a separate entry: the only way to
    know how far two identical runs differ, which is the smallest difference
    worth believing when two *different* runs are compared.
    """

    temperature: float = 0.0
    thinking: bool = False
    sample: int = 1
    # "holistic" (judge.py, one verdict in one go) or "requirements"
    # (requirements.py, one answer per requirement and the sum in code).
    method: str = "holistic"
    # The person's own rules, as the holistic judge is shown them
    # (preferences/prompt.py, 7.3). A different block is a different question,
    # so its digest is part of the name the answers are stored under.
    told: str | None = None

    def kind(self) -> str:
        if self.method == "requirements":
            # Not the score's version: the arithmetic runs again on every
            # read, so a stored answer outlives a change to it.
            name = f"requirement-judgement-{ANSWERS_VERSION}"
        else:
            name = f"judgement-{PROMPT_VERSION}"
        if self.temperature:
            name += f"-t{self.temperature:g}"
        if self.thinking:
            name += "-thinking"
        if self.told and self.method != "requirements":
            name += f"-told-{hashlib.sha256(self.told.encode()).hexdigest()[:8]}"
        if self.sample > 1:
            name += f"#{self.sample}"
        return name

    def judge(
        self,
        pairs: list[tuple[str, CVMatch]],
        client: ChatClient,
        cache: CVCache,
        *,
        model: str,
        mode: Mode,
        fresh: bool = False,
        requirements: CVCache | None = None,
    ) -> tuple[list[Judged], list[str], int]:
        """Judge what is not stored yet, reuse what is, and store as it arrives.

        Returns the judgements best first, the failures, and how many were paid
        for. A stored answer is the model's raw one and is checked again with
        today's `verify` -- and, for the requirement judge, added up again with
        today's weights -- so a change to either is measured for free.
        `requirements` is where the requirement judge keeps each vacancy's list.
        """
        kind = self.kind()
        ask, reread = None, rebuild
        if self.method == "requirements":
            book = RequirementBook(
                requirements or cache, client, model=model, mode=mode
            )

            def ask(cv_text: str, match: CVMatch) -> Judged:
                return judge_by_requirements(
                    cv_text,
                    match,
                    client,
                    book=book,
                    mode=mode,
                    temperature=self.temperature,
                )

            def reread(payload: dict, match: CVMatch, cv_text: str) -> Judged:
                answer = RequirementJudgement.model_validate(payload)
                return combine(answer, book.of(match), cv_text, match)

        stored, todo = [], []
        for cv_text, match in pairs:
            hit = None if fresh else cache.get(kind, model, pair_key(cv_text, match))
            if hit is None:
                todo.append((cv_text, match))
            else:
                stored.append(reread(hit, match, cv_text))

        # A judgement knows its vacancy and not its CV, and one CV can be in
        # many pairs, so the key is found through the match it was asked with.
        keys = {id(match): pair_key(cv_text, match) for cv_text, match in todo}

        def keep(one: Judged) -> None:
            payload = (one.raw or one.judgement).model_dump(mode="json")
            cache.put(kind, model, keys[id(one.match)], payload)

        fresh_ones, failures = judge_pairs(
            todo,
            client,
            mode=mode,
            temperature=self.temperature,
            on_judged=keep,
            judge=ask,
            told=self.told,
        )
        judged = sorted(stored + fresh_ones, key=lambda j: j.rank_key, reverse=True)
        return judged, failures, len(fresh_ones)

    def describe(self) -> str:
        judge = (
            f"requirement judge {REQUIREMENTS_VERSION}"
            if self.method == "requirements"
            else f"prompt {PROMPT_VERSION}"
        )
        thinking = "thinking on" if self.thinking else "thinking off"
        sample = f", sample {self.sample}" if self.sample > 1 else ""
        told = (
            ", told the person's own rules"
            if self.told and self.method != "requirements"
            else ""
        )
        return f"{judge}, temperature {self.temperature:g}, {thinking}{sample}{told}"


def pair_key(cv_text: str, match: CVMatch) -> str:
    """What a stored judgement is looked up by: exactly the two texts it read."""
    return f"{match.vacancy.key}\0{cv_text}\0{match.vacancy.text}"


def rebuild(payload: dict, match: CVMatch, cv_text: str) -> Judged:
    """A stored answer, checked again with today's `verify`."""
    raw = MatchJudgement.model_validate(payload)
    checked = verify(raw, cv_text, shown_vacancy(match))
    return Judged(
        match=match,
        judgement=checked.judgement,
        dropped=checked.dropped,
        quotes=checked.quotes,
        raw=raw,
    )


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
