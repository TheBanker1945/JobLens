"""Refresh where every Dutch place is on the map, for the distance preference.

"At most 40 km from home" needs a point for home and a point for the vacancy.
The CBS list in src/joblens/sources/places_nl.csv names every place and its
province but has no coordinates; this fetches the centre point of each place
(woonplaats) from PDOK's Locatieserver, the Dutch government's public geocoder
(Kadaster), and writes them beside the preferences code.

Source: PDOK Locatieserver v3.1, `type:woonplaats`, field `centroide_ll`
(WGS84 longitude/latitude). Open data (CC0), documented at
https://api.pdok.nl/bzk/locatieserver/search/v3_1/ui/. About 26 requests of
100 places, one second apart; run it when update_places.py is run.

Usage:
    uv run python scripts/update_place_coordinates.py
"""

import csv
import re
import sys
import time
from pathlib import Path

from joblens.sources.http import get_json, new_client

URL = "https://api.pdok.nl/bzk/locatieserver/search/v3_1/free"
PAGE = 100  # the Locatieserver's largest page
OUT = (
    Path(__file__).parent.parent
    / "src"
    / "joblens"
    / "preferences"
    / "places_nl_coordinates.csv"
)
POINT = re.compile(r"POINT\(([-\d.]+) ([-\d.]+)\)")


def main() -> int:
    rows, start = [], 0
    with new_client() as client:
        while True:
            answer = get_json(
                client,
                URL,
                "pdok",
                params={
                    "q": "*:*",
                    "fq": "type:woonplaats",
                    "fl": "woonplaatsnaam,gemeentenaam,provincienaam,centroide_ll",
                    "rows": PAGE,
                    "start": start,
                    "sort": "woonplaatsnaam asc",
                },
            )["response"]
            for doc in answer["docs"]:
                lon, lat = POINT.match(doc["centroide_ll"]).groups()
                rows.append(
                    (
                        doc["woonplaatsnaam"],
                        doc["gemeentenaam"],
                        doc["provincienaam"],
                        f"{float(lat):.5f}",
                        f"{float(lon):.5f}",
                    )
                )
            start += PAGE
            if start >= answer["numFound"]:
                break
            time.sleep(1)  # a public service: one page a second is plenty

    rows.sort()
    with OUT.open("w", encoding="utf-8", newline="") as handle:
        handle.write(
            "# PDOK Locatieserver woonplaats centroids (WGS84); CC0, Kadaster\n"
        )
        writer = csv.writer(handle)
        writer.writerow(("place", "municipality", "province", "lat", "lon"))
        writer.writerows(rows)
    print(f"{len(rows)} places with coordinates -> {OUT.name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
