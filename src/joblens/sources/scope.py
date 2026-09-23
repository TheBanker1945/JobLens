"""Which new vacancies JobLens keeps: the chosen provinces, the chosen kind of work.

Decided by Mahdi on 2026-09-22 (docs/vacancy-sources-phase-5.md): Zuid-Holland,
Noord-Holland, Utrecht and Zeeland; software and AI engineering, and everything
similar. The words and provinces live in the [scope] table of sources.toml, so
a fork with another region or another trade edits a table, not this file.

**Why filter at all.** Storing a vacancy is free; indexing it is not. Every
stored vacancy is extracted and embedded, at about 0.27 cent each (2.4), and a
nursing job in Eindhoven costs as much as a Python job in Delft.

**What it filters.** New vacancies, before they are stored. It never removes one
that is already there: the labels and evals of 3.1-3.5 refer to nursing and
technician vacancies, and they stay. Nothing is lost for good either: a vacancy
left out tonight is simply not stored, and the run report says how many and
which, so a wider scope picks them up on the next fetch.

**Where.** Every place name in the location text is looked up in the CBS list of
Dutch places (places_nl.csv, from scripts/update_places.py). A hand-written city
list would know Rotterdam and miss Goes, Breukelen and Schiphol. A location that
names no Dutch place at all ("Remote - Netherlands", or nothing) is kept: a
board that does not say where a job is cannot be used to argue it is somewhere
else -- the same rule as `same_place` in base.py.

**What kind of work.** The title, against two word lists. It must contain a
`roles` word and no `not_roles` word. Words of three letters or fewer ("ai",
"ml", "qa") must stand alone, or "ai" would match "detail" and "trainee"; longer
ones may sit inside a word, because Dutch writes "Softwareontwikkelaar" and
"Webdeveloper" as one.

**Where this is deliberately generous.** An unknown place is kept, and a place
name two provinces share is kept when either is chosen. Keeping one vacancy too
many costs a third of a cent; dropping the right one costs the job.
"""

import csv
import unicodedata
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path

from joblens.sources.base import Vacancy
from joblens.sources.netherlands import location_text

PLACES_CSV = Path(__file__).parent / "places_nl.csv"
LONGEST_NAME = 5  # words: "Nieuwerkerk aan den IJssel" is four

# How boards write provinces and a few places that the CBS list names otherwise.
# English is what Greenhouse and JobSpy use; the Dutch government name for The
# Hague is "'s-Gravenhage", which no job board uses.
ALIASES: Mapping[str, str] = {
    "noord holland": "Noord-Holland",
    "north holland": "Noord-Holland",
    "zuid holland": "Zuid-Holland",
    "south holland": "Zuid-Holland",
    "den haag": "Zuid-Holland",
    "the hague": "Zuid-Holland",
    "den bosch": "Noord-Brabant",
}
# Place names that, in a job location, always mean something else. Found by
# looking every stored location up, 2026-09-22: "Nederland" is a hamlet in
# Overijssel, and "Amsterdam, Noord-Holland, Nederland" is not a job there.
NOT_PLACES = frozenset({"nederland"})
# Province codes, only as a whole part of the location: "Breukelen, UT, NL"
# (JobSpy, 2026-09-22). As a loose word "ut" or "ze" could be anything.
CODES: Mapping[str, str] = {
    "nh": "Noord-Holland",
    "zh": "Zuid-Holland",
    "ut": "Utrecht",
    "ze": "Zeeland",
    "nb": "Noord-Brabant",
    "gld": "Gelderland",
    "ov": "Overijssel",
    "li": "Limburg",
    "fr": "Friesland",
    "gr": "Groningen",
    "dr": "Drenthe",
    "fl": "Flevoland",
}


def normalise(text: str) -> str:
    """Lower case, accents off, letters and digits only: "Súdwest-Fryslân" and
    "sudwest fryslan" are one name, and so are "'s-Gravenhage" and "s gravenhage"."""
    plain = unicodedata.normalize("NFKD", text)
    plain = "".join(char for char in plain if not unicodedata.combining(char))
    return " ".join(
        "".join(char if char.isalnum() else " " for char in plain.lower()).split()
    )


class Places:
    """Every Dutch place and municipality, and the province(s) it lies in."""

    def __init__(self, rows: Iterable[tuple[str, str, str]]):
        """CBS tells same-named places apart as "Rijswijk (NB)"; boards write
        "Rijswijk". So a bare name means the place CBS left without a suffix
        (Rijswijk in Zuid-Holland), and only when every copy has a suffix does
        the bare name mean all of them."""
        self.provinces: dict[str, set[str]] = {}
        suffixed: list[tuple[str, str]] = []
        for place, municipality, province in rows:
            for name in (place, municipality, province):
                bare = name.split(" (")[0]
                if bare == name:
                    self.provinces.setdefault(normalise(name), set()).add(province)
                else:
                    suffixed.append((normalise(bare), province))
        exact = set(self.provinces)
        for name, province in suffixed:
            if name not in exact:
                self.provinces.setdefault(name, set()).add(province)
        for name, province in ALIASES.items():
            self.provinces.setdefault(name, set()).add(province)
        for name in NOT_PLACES:
            self.provinces.pop(name, None)

    @classmethod
    def load(cls, path: Path = PLACES_CSV) -> "Places":
        with path.open(encoding="utf-8") as handle:
            lines = (line for line in handle if not line.startswith("#"))
            reader = csv.DictReader(lines)
            return cls(
                (row["place"], row["municipality"], row["province"]) for row in reader
            )

    def find(self, location: str) -> dict[str, set[str]]:
        """The Dutch places named in `location`, each with its province(s).

        Longest names first, so "Alphen aan den Rijn" (Zuid-Holland) is not read
        as "Alphen" (Noord-Brabant).
        """
        found: dict[str, set[str]] = {}
        for part in location.split(","):
            code = normalise(part)
            if code in CODES:
                found[code.upper()] = {CODES[code]}
                continue
            words = code.split()
            start = 0
            while start < len(words):
                for size in range(min(LONGEST_NAME, len(words) - start), 0, -1):
                    name = " ".join(words[start : start + size])
                    if name in self.provinces:
                        found[name] = self.provinces[name]
                        start += size
                        break
                else:
                    start += 1
        return found


@dataclass(frozen=True)
class Verdict:
    keep: bool
    reason: str  # one line, for the run report: why it was kept or left out


class Scope:
    """The [scope] table of sources.toml, as one question: keep this vacancy?"""

    def __init__(
        self,
        provinces: Iterable[str],
        roles: Iterable[str],
        not_roles: Iterable[str] = (),
        places: Places | None = None,
    ):
        self.chosen = set(provinces)
        self.roles = [normalise(word) for word in roles]
        self.not_roles = [normalise(word) for word in not_roles]
        self.places = places or Places.load()
        unknown = self.chosen - {
            province for found in self.places.provinces.values() for province in found
        }
        if unknown:
            raise ValueError(f"not a Dutch province: {', '.join(sorted(unknown))}")

    @classmethod
    def from_config(cls, table: Mapping, places: Places | None = None) -> "Scope":
        return cls(
            table["provinces"], table["roles"], table.get("not_roles", ()), places
        )

    def check(self, vacancy: Vacancy) -> Verdict:
        where = self.where(f"{vacancy.city or ''}, {location_text(vacancy)}")
        if where:
            return Verdict(False, where)
        return self.what(vacancy.title)

    def keep(self, vacancy: Vacancy) -> bool:
        return self.check(vacancy).keep

    def where(self, location: str) -> str:
        """Why this location is outside the scope, or "" if it is not."""
        found = self.places.find(location)
        if not found or any(provinces & self.chosen for provinces in found.values()):
            return ""
        named = ", ".join(
            f"{name} ({'/'.join(sorted(provinces))})"
            for name, provinces in found.items()
        )
        return f"outside the provinces: {named}"

    def what(self, title: str) -> Verdict:
        words = normalise(title)
        excluded = next((w for w in self.not_roles if contains(words, w)), None)
        if excluded:
            return Verdict(False, f"not the work: '{excluded}'")
        role = next((w for w in self.roles if contains(words, w)), None)
        if role:
            return Verdict(True, f"in scope: '{role}'")
        return Verdict(False, "not the work: no role word in the title")


def contains(title: str, word: str) -> bool:
    """Short words stand alone ("ai"); longer ones may sit inside a Dutch
    compound ("softwareontwikkelaar" contains "ontwikkelaar")."""
    if len(word) <= 3:
        return f" {word} " in f" {title} "
    return word in title
