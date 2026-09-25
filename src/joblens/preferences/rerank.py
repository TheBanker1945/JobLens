"""Stated preferences, applied to the ranking: a vacancy moves, it never goes.

Retrieval ranks every vacancy by how close it is to the CV. This checks each
one against what the person said they want, using the facts extraction already
read from the advert (contract, hours, work mode, salary, place, languages, the
level in the title, the employer), and moves the ones that contradict a stated
preference behind the ones that do not.

Three rules, all decided before this was written (phase-3 brief, 7.3 plan):

- **A preference moves a vacancy and never removes it.** A "permanent only"
  that deleted temporary jobs would delete them silently; moved, they stay in
  the stored ranking with their old place and the reason, and the page can say
  "this preference cost 12 vacancies their place" and show which.
- **Unknown costs nothing.** Of 1,425 extracted vacancies, 64% state no
  contract type, 53% no hours and 57% no salary. A vacancy that does not say is
  never moved for what it does not say.
- **Only what extraction knows.** Sectors, and whether to apply past a stated
  number of years, need reading, not a field: those go to the judge
  (prompt.py), and never here.

**How far a vacancy moves.** Each stated preference it contradicts puts it
behind every vacancy that contradicts fewer; among equals, retrieval's order is
kept. With a shortlist of ten out of 1,166, a contradicted vacancy is in effect
never judged -- which is what "I only want permanent" means -- while its place
and its reason stay on record.
"""

import re
from dataclasses import dataclass

from joblens.cv.match import LEGAL_FORM, CVMatch, employer
from joblens.extraction.schema import SalaryPeriod, VacancyDetails, WorkMode
from joblens.preferences.places import Geo, distance_km
from joblens.preferences.schema import Conflict, Preferences, Seniority
from joblens.sources.base import Vacancy, simplify

# The level a title names. "Medior/Senior Java Developer" names two, and
# contradicts a preference only if it names neither. Found in the corpus of
# 1,425: senior 210, junior 45, lead 43, medior 30, staff 13, head 7,
# principal 5, trainee 3.
LEVELS: dict[str, Seniority] = {
    "junior": Seniority.JUNIOR,
    "starter": Seniority.JUNIOR,
    "trainee": Seniority.JUNIOR,
    "medior": Seniority.MEDIOR,
    "senior": Seniority.SENIOR,
    "sr": Seniority.SENIOR,
    "lead": Seniority.LEAD,
    "principal": Seniority.LEAD,
    "staff": Seniority.LEAD,
    "head": Seniority.LEAD,
}

# Hours a week, when a salary is per hour and the advert does not say how many.
FULL_TIME = 40


@dataclass(frozen=True)
class Reranked:
    ranking: list[CVMatch]  # every vacancy, contradicted ones moved back
    conflicts: dict[str, list[Conflict]]  # by vacancy key; only those with any
    before: dict[str, int]  # retrieval's own position, 1-based, for every key


def rerank(
    ranking: list[CVMatch],
    details: dict[str, VacancyDetails],
    preferences: Preferences,
    geo: Geo | None = None,
) -> Reranked:
    geo = geo or Geo.load()
    home = geo.locate(preferences.home) if preferences.home else None
    found = [
        conflicts(match.vacancy, details.get(match.vacancy.key), preferences, geo, home)
        for match in ranking
    ]
    order = sorted(range(len(ranking)), key=lambda i: (len(found[i]), i))
    return Reranked(
        ranking=[ranking[i] for i in order],
        conflicts={
            ranking[i].vacancy.key: found[i] for i in range(len(ranking)) if found[i]
        },
        before={match.vacancy.key: i + 1 for i, match in enumerate(ranking)},
    )


def conflicts(
    vacancy: Vacancy,
    details: VacancyDetails | None,
    preferences: Preferences,
    geo: Geo,
    home=None,
) -> list[Conflict]:
    """Every stated preference this vacancy contradicts, from what it states."""
    if details is None:
        return []  # not extracted: it states nothing we can read
    p, found = preferences, []

    if p.contract_types and details.contract_type:
        if details.contract_type not in p.contract_types:
            found.append(
                Conflict(
                    field="contract",
                    found=details.contract_type.value,
                    wanted=_either(t.value for t in p.contract_types),
                )
            )

    if p.work_modes and details.work_mode:
        if details.work_mode not in p.work_modes:
            found.append(
                Conflict(
                    field="work_mode",
                    found=details.work_mode.value,
                    wanted=_either(m.value for m in p.work_modes),
                )
            )

    if (p.hours_min is not None or p.hours_max is not None) and (
        details.hours_min is not None or details.hours_max is not None
    ):
        low = details.hours_min if details.hours_min is not None else details.hours_max
        high = details.hours_max if details.hours_max is not None else low
        want_low = p.hours_min if p.hours_min is not None else 0
        want_high = p.hours_max if p.hours_max is not None else 99
        if high < want_low or low > want_high:
            found.append(
                Conflict(
                    field="hours",
                    found=_range(low, high, "hours a week"),
                    wanted=_range(p.hours_min, p.hours_max, "hours a week"),
                )
            )

    if p.salary_min is not None:
        month = monthly(details)
        if month is not None and month < p.salary_min:
            found.append(
                Conflict(
                    field="salary",
                    found=f"at most €{month:,.0f} a month",
                    wanted=f"at least €{p.salary_min:,} a month",
                )
            )

    if home and p.max_distance_km and details.work_mode is not WorkMode.REMOTE:
        there = geo.locate(details.city or vacancy.city)
        if there is not None:
            km = distance_km(home, there)
            if km > p.max_distance_km:
                found.append(
                    Conflict(
                        field="distance",
                        found=f"{km:.0f} km from {p.home} as the crow flies",
                        wanted=f"at most {p.max_distance_km} km",
                    )
                )

    if p.languages and details.languages_required:
        spoken = {one.lower() for one in p.languages}
        missing = [
            one for one in details.languages_required if one.lower() not in spoken
        ]
        if missing:
            found.append(
                Conflict(
                    field="language",
                    found=f"asks {' and '.join(missing)}",
                    wanted=_either(p.languages, "and"),
                )
            )

    if p.seniority:
        # The advert's own title: extraction sometimes rewrites it.
        named = title_levels(vacancy.title)
        if named and not named & set(p.seniority):
            found.append(
                Conflict(
                    field="seniority",
                    found=_either(sorted(level.value for level in named)),
                    wanted=_either(level.value for level in p.seniority),
                )
            )

    if p.avoid_employers:
        name = employer(vacancy, {vacancy.key: details})
        if name and any(_same_employer(name, avoid) for avoid in p.avoid_employers):
            found.append(
                Conflict(
                    field="employer",
                    found=vacancy.company or details.company or name,
                    wanted="not this employer",
                )
            )
    return found


def monthly(details: VacancyDetails) -> float | None:
    """The most the advert says it pays, per month, or None if it does not say.

    A year is twelve months (Dutch year figures often include holiday pay, so
    this is generous to the vacancy); an hour is multiplied by the hours it
    states, or a full-time week.
    """
    amount = (
        details.salary_max if details.salary_max is not None else details.salary_min
    )
    if amount is None or details.salary_period is None:
        return None
    if details.salary_period is SalaryPeriod.MONTH:
        return amount
    if details.salary_period is SalaryPeriod.YEAR:
        return amount / 12
    hours = details.hours_max or details.hours_min or FULL_TIME
    return amount * hours * 52 / 12


def title_levels(title: str) -> set[Seniority]:
    return {
        LEVELS[word] for word in re.findall(r"[a-z]+", title.lower()) if word in LEVELS
    }


def _same_employer(name: str, avoid: str) -> bool:
    """Avoiding "ING" avoids "ING" and "ING Bank", and not "Ingenico"."""
    wanted = LEGAL_FORM.sub("", simplify(avoid))
    return bool(wanted) and (name == wanted or name.startswith(f"{wanted} "))


def _either(values, word: str = "or") -> str:
    values = list(values)
    return (
        values[0]
        if len(values) == 1
        else f"{', '.join(values[:-1])} {word} {values[-1]}"
    )


def _range(low: int | None, high: int | None, unit: str) -> str:
    if low is not None and high is not None:
        return f"{low} {unit}" if low == high else f"{low}-{high} {unit}"
    return f"at least {low} {unit}" if low is not None else f"at most {high} {unit}"
