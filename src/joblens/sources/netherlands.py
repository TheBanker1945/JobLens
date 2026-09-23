"""Which fetched vacancies are in the Netherlands, and how many of them to keep.

Greenhouse boards are worldwide -- Databricks lists 883 jobs, 27 of them Dutch --
and give no country, so the location text is all there is to go on. This check
used to live in scripts/fetch_vacancies.py, *after* a `--limit` that the boards
applied first: the first 100 jobs of a worldwide board, and then the Dutch ones
among those. Measured 2026-09-22, that kept 29 of Adyen's 56 Dutch vacancies and
5 of Databricks' 27, and the run report said "listed 100" as if nothing had
happened.

**The order is the fix.** A limit only saves something when it comes before the
expensive step. For a board the download is already done by then -- one request,
the whole board -- and the expensive step after it is extraction, which costs
money per vacancy. So the cap sits between the filter and the store, and it
lives here rather than in the script so that the order is something a test can
check.

Since 5.2 there are two filters before the cap: Dutch, then the scope (the
provinces and the kind of work, sources/scope.py). The same lesson twice: a cap
that runs before the scope spends its places on nursing jobs in Eindhoven.
"""

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from joblens.sources.base import Vacancy

if TYPE_CHECKING:  # scope.py imports location_text from here
    from joblens.sources.scope import Scope

COUNTRY_NAMES = ("NL", "NLD", "NETHERLANDS")


def is_dutch(vacancy: Vacancy, markers: list[str]) -> bool:
    """A Dutch country code, or a Dutch place in the location text.

    `markers` comes from sources.toml: "Amsterdam", "Nederland", and so on.
    """
    if (vacancy.country or "").upper() in COUNTRY_NAMES:
        return True
    place = f"{vacancy.city or ''} {location_text(vacancy)}".lower()
    return any(marker.lower() in place for marker in markers)


def location_text(vacancy: Vacancy) -> str:
    """The location as the source wrote it: a string, or {"name": "Amsterdam"}."""
    location = vacancy.raw.get("location")
    if isinstance(location, dict):
        return str(location.get("name", ""))
    return str(location or "")


@dataclass(frozen=True)
class Selection:
    """What goes to the store, and what the two steps before it took out."""

    kept: list[Vacancy]
    dutch: int  # passed the Dutch filter
    capped: int  # passed both filters, then left out by the cap
    out_of_scope: int = 0  # Dutch, but not in the scope
    left_out: list[str] = field(default_factory=list)  # "title -- why", per job


def select(
    vacancies: list[Vacancy],
    markers: list[str],
    *,
    limit: int | None,
    all_countries: bool = False,
    scope: "Scope | None" = None,
) -> Selection:
    """The Dutch vacancies, then those in scope, then at most `limit`. In order.

    `capped` and `out_of_scope` are counted rather than silently applied: a
    filter that bites looks exactly like a quiet board unless somebody says so.
    """
    dutch = (
        vacancies
        if all_countries
        else [vacancy for vacancy in vacancies if is_dutch(vacancy, markers)]
    )
    wanted, left_out = dutch, []
    if scope is not None:
        wanted = []
        for vacancy in dutch:
            verdict = scope.check(vacancy)
            if verdict.keep:
                wanted.append(vacancy)
            else:
                left_out.append(f"{vacancy.title} -- {verdict.reason}")
    kept = wanted if limit is None else wanted[:limit]
    return Selection(
        kept=kept,
        dutch=len(dutch),
        capped=len(wanted) - len(kept),
        out_of_scope=len(dutch) - len(wanted),
        left_out=left_out,
    )
