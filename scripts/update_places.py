"""Refresh the list of Dutch places and their provinces from CBS open data.

The scope filter (src/joblens/sources/scope.py) decides whether a vacancy is in
one of the chosen provinces by looking its place up in this list. A hand-written
list of cities would know Rotterdam and miss Goes, Breukelen and Schiphol; this
one is every place (woonplaats) in the country, 2,502 of them on 1 January 2026,
each with its municipality and province.

Source: CBS StatLine table 86312NED, "Woonplaatsen in Nederland 2026", via the
CBS OData API. CBS publishes it as open data; attribution: Centraal Bureau voor
de Statistiek. CBS compiles a new table every spring: change TABLE and run this
again.

Usage:
    uv run python scripts/update_places.py
"""

import csv
import sys
from pathlib import Path

from joblens.sources.http import get_json, new_client

TABLE = "86312NED"
BASE = f"https://opendata.cbs.nl/ODataApi/OData/{TABLE}"
OUT = Path(__file__).parent.parent / "src" / "joblens" / "sources" / "places_nl.csv"


def main() -> int:
    with new_client() as client:
        names = {
            row["Key"]: row["Title"].strip()
            for row in get_json(client, f"{BASE}/Woonplaatsen", "cbs")["value"]
        }
        rows = get_json(client, f"{BASE}/TypedDataSet", "cbs")["value"]

    places = sorted(
        {
            (names[row["Woonplaatsen"]], row["Naam_2"].strip(), row["Naam_4"].strip())
            for row in rows
        }
    )
    with OUT.open("w", encoding="utf-8", newline="") as handle:
        handle.write(f"# CBS {TABLE}, Woonplaatsen in Nederland; open data, (c) CBS\n")
        writer = csv.writer(handle)
        writer.writerow(("place", "municipality", "province"))
        writer.writerows(places)
    provinces = len({province for _, _, province in places})
    print(f"{len(places)} places in {provinces} provinces -> {OUT.name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
