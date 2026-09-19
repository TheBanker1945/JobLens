"""Score an extracted VacancyDetails against a hand-labelled one, field by field.

Scalar fields get one of five outcomes. The split matters: for a job-matching app a
hallucinated salary (invented) is worse than a missed one (left null).

    correct       both have a value and they match
    correct_null  both null: the model correctly said "not in the text"
    wrong         both have a value, but they differ
    missed        the text states it, the model returned null
    hallucinated  the text does not state it, the model returned a value

List fields (skills, languages) get precision, recall and F1, so partly right
counts for something.
"""

from typing import Any, Literal

from pydantic import BaseModel

from joblens.extraction.schema import VacancyDetails

Outcome = Literal["correct", "correct_null", "wrong", "missed", "hallucinated"]

LIST_FIELDS = ("skills", "languages_required")
# Free-text fields where any wording is fine: only "present or null" is scored.
PRESENCE_ONLY_FIELDS = ("salary_note",)


class ScalarScore(BaseModel):
    field: str
    outcome: Outcome
    expected: Any
    predicted: Any


class ListScore(BaseModel):
    field: str
    precision: float  # share of predicted items that are correct
    recall: float  # share of expected items that were found
    f1: float  # harmonic mean of the two
    missing: list[str]
    extra: list[str]


class SampleScore(BaseModel):
    scalars: list[ScalarScore]
    lists: list[ListScore]

    def count(self, outcome: Outcome) -> int:
        return sum(s.outcome == outcome for s in self.scalars)

    @property
    def accuracy(self) -> float:
        good = self.count("correct") + self.count("correct_null")
        return good / len(self.scalars)


def score_details(
    expected: VacancyDetails,
    predicted: VacancyDetails,
    alternatives: dict[str, list[Any]] | None = None,
) -> SampleScore:
    """`alternatives` lists other acceptable values per field, e.g. a company name
    with and without "B.V."."""
    alternatives = alternatives or {}
    exp, pred = expected.model_dump(mode="json"), predicted.model_dump(mode="json")
    scalars, lists = [], []
    for field in VacancyDetails.model_fields:
        if field in LIST_FIELDS:
            lists.append(score_list(field, exp[field], pred[field]))
        else:
            accepted = [exp[field], *alternatives.get(field, [])]
            outcome = score_scalar(
                accepted, pred[field], presence_only=field in PRESENCE_ONLY_FIELDS
            )
            scalars.append(
                ScalarScore(
                    field=field,
                    outcome=outcome,
                    expected=exp[field],
                    predicted=pred[field],
                )
            )
    return SampleScore(scalars=scalars, lists=lists)


def score_scalar(
    accepted: list[Any], predicted: Any, *, presence_only: bool = False
) -> Outcome:
    """`accepted[0]` is the labelled value; the rest are acceptable alternatives."""
    expected = accepted[0]
    if expected is None:
        return "correct_null" if predicted is None else "hallucinated"
    if predicted is None:
        return "missed"
    if presence_only:
        return "correct"
    target = normalize(predicted)
    return "correct" if any(normalize(a) == target for a in accepted) else "wrong"


def score_list(field: str, expected: list[str], predicted: list[str]) -> ListScore:
    exp = {normalize(x) for x in expected}
    pred = {normalize(x) for x in predicted}
    hits = len(exp & pred)
    # Empty on both sides is a perfect answer; empty on one side scores 0.
    precision = hits / len(pred) if pred else float(not exp)
    recall = hits / len(exp) if exp else float(not pred)
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return ListScore(
        field=field,
        precision=precision,
        recall=recall,
        f1=f1,
        missing=sorted(exp - pred),
        extra=sorted(pred - exp),
    )


def normalize(value: Any) -> Any:
    """Compare meaning, not formatting: 3200 == 3200.0, 'Utrecht ' == 'utrecht'."""
    if isinstance(value, bool):
        return value
    if isinstance(value, int | float):
        return round(float(value), 2)
    if isinstance(value, str):
        return " ".join(value.casefold().split()).rstrip(".")
    return value
