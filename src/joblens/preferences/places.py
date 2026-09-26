"""Where a place is, and how far apart two places are, as the crow flies.

The distance preference ("at most 40 km from home") is measured in a straight
line between the centre points of two places (places_nl_coordinates.csv, from
PDOK; scripts/update_place_coordinates.py). That is not travel time: Leiden to
Utrecht is 42 km on the map and most of an hour by train. It is what can be
computed for every vacancy for free, and the preference says what it is --
"hemelsbreed" -- rather than pretending to be a journey planner.

**An unknown place costs nothing.** A location that names no Dutch place, or
names one of the 70 place names the country has more than once and far apart
("Beek" is in Gelderland, Limburg and Noord-Brabant), gives no point, and a
vacancy without a point is never moved for being far away. Same rule as the
scope filter: a board that does not say where a job is cannot be used to argue
that it is somewhere else.
"""

import csv
import math
from collections import defaultdict
from dataclasses import dataclass
from functools import cache
from pathlib import Path

from joblens.sources.scope import LONGEST_NAME, NOT_PLACES, normalise

COORDINATES_CSV = Path(__file__).parent / "places_nl_coordinates.csv"

# Several places under one name within this distance are one point, their
# middle: "Rijswijk" the town and "Rijswijk" the municipality. Further apart,
# the name is ambiguous and gives no point at all.
SAME_PLACE_KM = 15.0

# What job boards write for places PDOK names otherwise.
ALIASES = {
    "den haag": "s gravenhage",
    "the hague": "s gravenhage",
    "den bosch": "s hertogenbosch",
}


@dataclass(frozen=True)
class Point:
    lat: float
    lon: float


def distance_km(a: Point, b: Point) -> float:
    """Great-circle distance (haversine): exact enough for a country 300 km wide."""
    lat1, lat2 = math.radians(a.lat), math.radians(b.lat)
    dlat, dlon = lat2 - lat1, math.radians(b.lon - a.lon)
    h = (
        math.sin(dlat / 2) ** 2
        + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    )
    return 2 * 6371.0 * math.asin(math.sqrt(h))


class Geo:
    """Dutch place and municipality names, each with one point or none."""

    def __init__(self, rows: list[tuple[str, str, float, float]]):
        by_name: dict[str, list[Point]] = defaultdict(list)
        by_municipality: dict[str, list[Point]] = defaultdict(list)
        for place, municipality, lat, lon in rows:
            point = Point(lat, lon)
            # "Hengelo (Gld)" is also findable as "Hengelo" -- unless a place
            # is called exactly that, which then wins, as in scope.Places.
            by_name[normalise(place)].append(point)
            bare = place.split(" (")[0]
            if bare != place:
                by_name.setdefault(f"~{normalise(bare)}", []).append(point)
            by_municipality[normalise(municipality.split(" (")[0])].append(point)

        self.points: dict[str, Point | None] = {}
        for name, points in by_name.items():
            if not name.startswith("~"):
                self.points[name] = _one(points)
        for name, points in by_name.items():
            if name.startswith("~") and name[1:] not in self.points:
                self.points[name[1:]] = _one(points)
        for name, points in by_municipality.items():
            # A municipality is only a fallback: "Utrecht" is the city.
            self.points.setdefault(name, _middle(points))
        for alias, name in ALIASES.items():
            self.points[alias] = self.points.get(name)
        for name in NOT_PLACES:
            self.points.pop(name, None)

    @classmethod
    def load(cls, path: Path = COORDINATES_CSV) -> "Geo":
        return _load(path)

    def locate(self, location: str | None) -> Point | None:
        """The first Dutch place `location` names, or None.

        Longest names first, part by part, as scope.Places.find reads a
        location: "Alphen aan den Rijn, Zuid-Holland" is Alphen aan den Rijn.
        """
        if not location:
            return None
        for part in location.split(","):
            words = normalise(part).split()
            for start in range(len(words)):
                for size in range(min(LONGEST_NAME, len(words) - start), 0, -1):
                    name = " ".join(words[start : start + size])
                    if name in self.points:
                        return self.points[name]  # None when it is ambiguous
        return None

    def knows(self, place: str) -> bool:
        """Whether `place` names exactly one point: what a home has to be."""
        return self.points.get(normalise(place)) is not None


# Written by people, not by PDOK: offered beside the official names.
SPOKEN_NAMES = ("Den Haag", "Den Bosch")


@cache
def place_names(path: Path = COORDINATES_CSV) -> tuple[str, ...]:
    """Every place a home can be, as a person would pick it from a list: the
    official names (with "Hengelo (Gld)" and the like), plus Den Haag and Den
    Bosch. Only names Geo can place, so every suggestion is a valid answer."""
    geo = Geo.load(path)
    with path.open(encoding="utf-8") as handle:
        lines = (line for line in handle if not line.startswith("#"))
        names = {row["place"] for row in csv.DictReader(lines)}
    names |= set(SPOKEN_NAMES)
    return tuple(sorted((name for name in names if geo.knows(name)), key=str.casefold))


@cache
def _load(path: Path) -> Geo:
    with path.open(encoding="utf-8") as handle:
        lines = (line for line in handle if not line.startswith("#"))
        return Geo(
            [
                (
                    row["place"],
                    row["municipality"],
                    float(row["lat"]),
                    float(row["lon"]),
                )
                for row in csv.DictReader(lines)
            ]
        )


def _one(points: list[Point]) -> Point | None:
    middle = _middle(points)
    if all(distance_km(middle, point) <= SAME_PLACE_KM for point in points):
        return middle
    return None


def _middle(points: list[Point]) -> Point:
    return Point(
        sum(p.lat for p in points) / len(points),
        sum(p.lon for p in points) / len(points),
    )
