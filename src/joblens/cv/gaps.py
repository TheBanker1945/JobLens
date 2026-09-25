"""What keeps coming up that you do not have, across a whole run.

The brief calls this "the single most useful sentence this app can say to me". A
per-vacancy gap list is already there from 3.6 -- every gap carries a requirement,
a `required`/nice-to-have flag, and a quote from the vacancy that was **checked
against the vacancy text** before anyone saw it. This module does the arithmetic
over a run of them: which requirement appears in how many of the vacancies you
came closest to, and how much that should weigh.

**No model call, and that is a decision rather than thrift.** Grouping "Azure" and
"Microsoft Azure" is the one part of this that looks like it wants a model, and
the extraction we already pay for hands it over for free: `VacancyDetails.skills`
is a canonical list of short skill names for every vacancy in the corpus. So a
skill term out of that vocabulary is the counting unit, a gap joins a term's
bucket when it names that term, and nothing is grouped that no vacancy called a
skill. The alternative -- embedding each gap sentence and clustering -- groups
paraphrases this misses, but a cluster has no name, so something has to invent
one, and an invented label is the only thing on this screen with no source text
behind it. Every line here can be traced to a vacancy quote instead.

The comparison is `cv/verify.py:searchable`, the same normalisation the quote
check uses, for the same reason: removing formatting cannot make two different
claims look alike, and nothing looser than that is allowed anywhere in this app.

Two blocks come out:

- the grouped judge gaps, weighted by how good the match was;
- three field-level checks (education level, languages, years) that need no judge
  at all, because the vacancies were extracted into fields in phase 1.
"""

import re
from collections import Counter
from dataclasses import dataclass, field

from joblens.cv.judge import Judged, Verdict
from joblens.cv.schema import CVProfile, meets_level
from joblens.cv.verify import searchable
from joblens.extraction.schema import VacancyDetails

# A one- or two-character "skill" ("C", "R", "AI") matches inside half the Dutch
# language once you are looking for whole tokens in a sentence. They are dropped
# from the vocabulary rather than special-cased: a term too short to be found
# safely is a term this method cannot count, and the gap falls into `ungrouped`
# where it is visible instead of into the wrong bucket.
#
# Except a short term that carries a symbol: "C#" and "F#" are no Dutch word,
# and "C#" is the gap that kept recurring on the first real CV.
MIN_TERM_CHARS = 3
SYMBOL = re.compile(r"[#+]")

# What joins alternatives: "Airflow of Dagster", "AWS, GCP or Azure",
# "Airflow/Dagster". Only looked for *between* two named skills, so "knowledge
# of Python" -- English "of" in front of the list -- is not an alternative.
ALTERNATIVE = re.compile(r"\b(?:or|of|ofwel|and/or|en/of)\b|/")


@dataclass(frozen=True)
class Occurrence:
    """One judged vacancy asking for one thing the CV does not show."""

    key: str
    title: str
    company: str | None
    requirement: str  # the judge's words
    quote: str  # the vacancy's words, verified against the vacancy text
    required: bool
    fit: int
    verdict: Verdict

    @property
    def weight(self) -> float:
        """How much this occurrence counts: the match's own fit, out of one.

        "The vacancies I came closest to" is the brief's phrase, so a gap in a
        vacancy judged 90 has to weigh more than the same gap in one judged 20.
        fit/100 is the whole rule -- no tuned constants, and the number it is
        made of is printed next to it.
        """
        return self.fit / 100


@dataclass
class Group:
    """One thing the shortlist keeps asking for."""

    term: str  # the display name: the spelling the vacancies used most often
    occurrences: list[Occurrence] = field(default_factory=list)
    spellings: Counter = field(default_factory=Counter)

    def per_vacancy(self) -> list[Occurrence]:
        """One occurrence per vacancy, and the reason every count below uses it.

        A vacancy that names Python under "wat je meebrengt" and again under
        "wat je gaat doen" produces two gaps, and counting both would print "in
        2 of 10" about one advert. The question is how many of the vacancies ask
        for this, so a vacancy votes once -- with its strongest wording, because
        a requirement stated anywhere in an advert is a requirement.
        """
        best: dict[str, Occurrence] = {}
        for one in self.occurrences:
            current = best.get(one.key)
            if current is None or (one.required, one.fit) > (
                current.required,
                current.fit,
            ):
                best[one.key] = one
        return list(best.values())

    @property
    def count(self) -> int:
        return len(self.per_vacancy())

    @property
    def required_count(self) -> int:
        return sum(1 for one in self.per_vacancy() if one.required)

    @property
    def weight(self) -> float:
        return sum(one.weight for one in self.per_vacancy())

    @property
    def average_fit(self) -> float:
        found = self.per_vacancy()
        return sum(one.fit for one in found) / len(found)

    def example(self) -> Occurrence:
        """The occurrence worth printing: the best-fitting vacancy that asks for
        it, preferring one that states it as a requirement."""
        return max(self.occurrences, key=lambda one: (one.required, one.fit))


@dataclass(frozen=True)
class FieldGap:
    """A requirement counted from extracted fields, with no judge involved."""

    label: str
    count: int
    total: int
    detail: str


@dataclass(frozen=True)
class GapSummary:
    judged: int  # how many vacancies these gaps were collected from
    groups: list[Group]  # sorted by weight, heaviest first
    ungrouped: list[Occurrence]  # named nothing this corpus calls a skill
    already_on_cv: list[Occurrence]  # the judge asked for something the CV lists
    fields: list[FieldGap]

    @property
    def total_gaps(self) -> int:
        """Every gap the judge produced, counted as it was produced.

        Not `sum(group.count)`, which deduplicates a vacancy that says the same
        thing twice: the share below is about how well the grouping worked, so
        its denominator has to be what the grouping was given.
        """
        return (
            sum(len(group.occurrences) for group in self.groups)
            + len(self.ungrouped)
            + len(self.already_on_cv)
        )

    @property
    def grouped_share(self) -> float:
        """How much of the run this method could count.

        The number that decides whether a model call is worth buying later. High,
        and the vocabulary is doing the work; low, and paraphrases are escaping it
        and there is something to fix. Printed, never assumed.
        """
        if not self.total_gaps:
            return 1.0
        grouped = sum(len(group.occurrences) for group in self.groups)
        return grouped / self.total_gaps


def summarise_gaps(
    judged: list[Judged],
    profile: CVProfile,
    details: dict[str, VacancyDetails],
    cv_text: str = "",
) -> GapSummary:
    """The gap lists of a whole run, counted and weighted. Reads no model.

    `cv_text` is the redacted CV the judge read. A term it names as whole words
    counts as on the CV even when the extracted skill list missed it: the list
    is a model's reading of the CV, and the text is the CV.
    """
    vocabulary = _vocabulary(judged, details)
    on_cv = _cv_terms(profile)
    cv = searchable(cv_text)

    groups: dict[str, Group] = {}
    ungrouped: list[Occurrence] = []
    already_on_cv: list[Occurrence] = []

    for one in judged:
        for gap in one.judgement.gaps:
            occurrence = Occurrence(
                key=one.match.vacancy.key,
                title=one.match.vacancy.title,
                company=one.match.vacancy.company,
                requirement=gap.requirement,
                quote=gap.vacancy_quote,
                required=gap.required,
                fit=one.judgement.fit,
                verdict=one.judgement.verdict,
            )
            term = _term_for(occurrence, vocabulary)
            if term is None:
                ungrouped.append(occurrence)
            elif term in on_cv or (cv and _names(cv, term)):
                # The headline is "what keeps coming up that you do not have", so
                # something the CV lists cannot be in it whatever the judge said.
                # Kept and shown apart, because a judge asking for a skill the CV
                # states is either wanting more than a mention of it or wrong, and
                # both are worth seeing.
                already_on_cv.append(occurrence)
            else:
                group = groups.setdefault(term, Group(term=vocabulary[term]))
                group.occurrences.append(occurrence)
                group.spellings[vocabulary[term]] += 1

    _merge_compounds(groups)
    for group in groups.values():
        group.term = group.spellings.most_common(1)[0][0]

    return GapSummary(
        judged=len(judged),
        groups=sorted(groups.values(), key=lambda g: (-g.weight, g.term)),
        ungrouped=ungrouped,
        already_on_cv=already_on_cv,
        fields=_field_gaps(judged, profile, details),
    )


def _vocabulary(
    judged: list[Judged], details: dict[str, VacancyDetails]
) -> dict[str, str]:
    """Every skill name the judged vacancies use, normalised -> as written.

    The union over the shortlist rather than each vacancy's own list: a gap on
    one vacancy is often phrased in the words another vacancy names as a skill,
    and the quote still comes from the vacancy that produced the gap, so nothing
    is attributed to the wrong advert.
    """
    vocabulary: dict[str, str] = {}
    for one in judged:
        found = details.get(one.match.vacancy.key)
        if found is None:
            continue
        for skill in found.skills:
            normalised = searchable(skill)
            if len(normalised) >= MIN_TERM_CHARS or SYMBOL.search(normalised):
                vocabulary.setdefault(normalised, skill.strip())
    return vocabulary


def _cv_terms(profile: CVProfile) -> set[str]:
    """The CV's own skills, normalised the same way, for the exclusion above."""
    return {
        searchable(skill)
        for skill in [*profile.all_skills(), *profile.certificates]
        if searchable(skill)
    }


def _term_for(occurrence: Occurrence, vocabulary: dict[str, str]) -> str | None:
    """The longest vocabulary term this gap names, or None if it names none.

    The judge's requirement is asked first and the vacancy quote only when the
    requirement names nothing. A quote is the vacancy's whole sentence, and it
    often lists several skills: "C#, TypeScript en SQL Server". Searching both
    at once and taking the longest filed a "C# .NET" gap under TypeScript --
    which the first real CV lists, so the gap then moved to "already on your
    CV" and out of the headline, in every one of its three runs (2026-09-22
    audit: 53 of 279 stored gaps filed under a term their requirement did not
    name).

    Longest first so that a gap saying "Microsoft Azure" is not filed under a
    shorter term it happens to contain; `_merge_compounds` then folds the long
    one into the short one when both exist, which is where "Microsoft Azure" and
    "Azure" actually become one line.
    """
    for text in (occurrence.requirement, occurrence.quote):
        haystack = searchable(text)
        found = [term for term in vocabulary if _names(haystack, term)]
        if found:
            return None if _alternatives(haystack, found) else max(found, key=len)
    return None


def _alternatives(haystack: str, found: list[str]) -> bool:
    """Whether the gap names several skills as a choice between them.

    "Fabric, Synapse, Databricks or Snowflake" asks for any one of four, so
    filing it under the longest name told a person that Databricks keeps
    coming up when no vacancy required it -- 153 of the 943 gaps stored by
    2026-09-24 (16%) read that way. Such a gap cannot be counted under one skill,
    so it is not: it goes to `ungrouped`, where it is still shown, as this
    module does with every gap it cannot attribute safely.

    Only what stands between the skills is read, with the skills themselves
    masked, so the "/" inside "CI/CD" is not a choice. A list joined by "and"
    keeps the old behaviour.
    """
    # A term inside a longer one it was found with is one skill, not two:
    # "azure" in "azure devops".
    distinct = [t for t in found if not any(t != o and t in o for o in found)]
    if len(distinct) < 2:
        return False
    masked = haystack
    for term in sorted(distinct, key=len, reverse=True):
        masked = re.sub(rf"(?<![\w+#]){re.escape(term)}(?![\w+#])", "\0", masked)
    between = masked[masked.find("\0") : masked.rfind("\0")]
    return ALTERNATIVE.search(between) is not None


def _names(haystack: str, term: str) -> bool:
    """Whether `term` appears in `haystack` as whole tokens.

    Whole tokens, so "sql" does not match inside "mysql" and "java" does not
    match inside "javascript" -- the two mistakes that would quietly merge
    unrelated requirements and make the count say something false.
    """
    return re.search(rf"(?<![\w+#]){re.escape(term)}(?![\w+#])", haystack) is not None


def _merge_compounds(groups: dict[str, Group]) -> None:
    """Fold "microsoft azure" into "azure" when both are buckets of this run.

    The one grouping step that is not a literal match, and it is deliberately
    narrow: a bucket is merged into another only when the other's tokens are all
    of its own tokens, and only when that other bucket already exists because
    some gap produced it. So "Microsoft Azure" joins "Azure", and nothing joins a
    term the run never raised on its own.
    """
    for term in sorted(groups, key=len, reverse=True):
        if term not in groups:
            continue
        tokens = set(term.split())
        hosts = [
            other
            for other in groups
            if other != term and set(other.split()) < tokens  # a strict subset
        ]
        if not hosts:
            continue
        host = groups[min(hosts, key=len)]
        merged = groups.pop(term)
        host.occurrences.extend(merged.occurrences)
        host.spellings.update(merged.spellings)


def _field_gaps(
    judged: list[Judged],
    profile: CVProfile,
    details: dict[str, VacancyDetails],
) -> list[FieldGap]:
    """Three counts that need no judge: level, languages, years.

    Phase 1 extracted these into fields for every vacancy in the corpus, and a
    field comparison cannot hallucinate. It is also the only part of this report
    that would still work if the judge were switched off entirely.
    """
    found = [
        details[one.match.vacancy.key]
        for one in judged
        if one.match.vacancy.key in details
    ]
    if not found:
        return []
    total = len(found)
    gaps: list[FieldGap] = []

    level = profile.highest_level()
    short = [one for one in found if not meets_level(one.education_level, profile)]
    if short:
        asked = Counter(
            one.education_level.value for one in short if one.education_level
        )
        gaps.append(
            FieldGap(
                "opleidingsniveau",
                len(short),
                total,
                f"they ask {'/'.join(sorted(asked))}; your CV shows "
                + (level.value if level else "no level"),
            )
        )

    spoken = {searchable(language) for language in profile.languages}
    missing = Counter(
        language
        for one in found
        for language in one.languages_required
        if searchable(language) not in spoken
    )
    if missing:
        gaps.append(
            FieldGap(
                "taal",
                sum(missing.values()),
                total,
                "required and not listed on your CV: "
                + ", ".join(f"{name} ({n}x)" for name, n in missing.most_common()),
            )
        )

    years = profile.years_of_experience()
    if years is not None:
        over = [
            one
            for one in found
            if one.experience_years_min is not None and one.experience_years_min > years
        ]
        if over:
            most = max(one.experience_years_min for one in over)
            gaps.append(
                FieldGap(
                    "jaren ervaring",
                    len(over),
                    total,
                    f"they state a minimum above the {years:g} years your dates "
                    f"cover, up to {most}",
                )
            )
    return gaps
