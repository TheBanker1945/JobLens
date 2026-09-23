"""schema.org JobPosting: the structured data an employer's own site publishes.

Google for Jobs only lists a vacancy page that carries a JobPosting in JSON-LD,
so most Dutch career sites ("werkenbij..." domains, whatever system runs them)
put one on every vacancy page: title, employer, place, dates, and usually the
whole text. One reader for that shape covers sites that no adapter will ever
name. This module is that reader, and nothing about any one site.

What sites actually send differs more than the schema suggests, and every
variant below is one to expect rather than an error:

- the posting at the top level, in a list, or inside an `@graph`;
- `@type` as "JobPosting" or as a list that contains it;
- `hiringOrganization` as a name or as an Organization;
- `jobLocation` as one Place or a list of them, with the city in
  `address.addressLocality` and sometimes only `addressRegion`;
- `identifier` as a string or a PropertyValue (`{"value": ...}`);
- dates as "2026-09-04" or a full timestamp;
- a `description` that is the whole vacancy in HTML, or a teaser: werkenvoor
  nederland.nl sends 104 characters (2026-09-22), so a text under the usual
  floor is refused, never stored as if it were a vacancy.
"""

import hashlib
import json
from collections.abc import Iterator
from datetime import UTC, datetime
from typing import Any

from joblens.sources.base import Vacancy
from joblens.sources.clean import extract_elements, redact, to_clean_text
from joblens.sources.scraped import MIN_TEXT_CHARS

DUTCH = frozenset({"NL", "NLD", "NETHERLANDS", "NEDERLAND", "THE NETHERLANDS"})


def job_postings(page: str) -> list[dict]:
    """Every JobPosting in the page's JSON-LD blocks. A block that is not valid
    JSON is skipped: one broken block must not hide a good one next to it."""
    blocks = extract_elements(
        page, lambda attrs: (attrs.get("type") or "").lower() == "application/ld+json"
    )
    found = []
    for block in blocks:
        try:
            data = json.loads(block)
        except ValueError:
            continue
        found.extend(_postings_in(data))
    return found


def _postings_in(data: Any) -> Iterator[dict]:
    if isinstance(data, list):
        for item in data:
            yield from _postings_in(item)
    elif isinstance(data, dict):
        kind = data.get("@type")
        kinds = kind if isinstance(kind, list) else [kind]
        if "JobPosting" in kinds:
            yield data
        elif "@graph" in data:
            yield from _postings_in(data["@graph"])


def place_of(posting: dict) -> tuple[str | None, str, str | None]:
    """(city, location text for the scope, country code) from `jobLocation`."""
    places = posting.get("jobLocation") or []
    places = places if isinstance(places, list) else [places]
    parts, city, country = [], None, None
    for place in places:
        address = place.get("address", {}) if isinstance(place, dict) else {}
        if isinstance(address, str):
            parts.append(address)
            continue
        locality = address.get("addressLocality")
        city = city or locality
        parts += [p for p in (locality, address.get("addressRegion")) if p]
        found = address.get("addressCountry")
        if isinstance(found, dict):
            found = found.get("name")
        country = country or (str(found).upper() if found else None)
    if posting.get("jobLocationType") == "TELECOMMUTE":
        parts.append("Remote")
    return city, ", ".join(str(p) for p in parts), country


def posting_to_vacancy(
    posting: dict, *, source: str, url: str, now: datetime | None = None
) -> Vacancy | None:
    """A Vacancy, or None for a posting that is expired or holds no real text."""
    now = now or datetime.now(UTC)
    closes = parse_date(posting.get("validThrough"))
    if closes is not None and closes < now:
        return None  # the employer says it closed; the page just has not gone
    text = to_clean_text(str(posting.get("description") or ""))
    if len(text) < MIN_TEXT_CHARS:
        return None
    organisation = posting.get("hiringOrganization")
    if isinstance(organisation, dict):
        organisation = organisation.get("name")
    city, location, country = place_of(posting)
    return Vacancy(
        source=source,
        source_id=identifier_of(posting) or _hash(url),
        url=str(posting.get("url") or url),
        title=str(posting.get("title") or "").strip(),
        company=str(organisation).strip() if organisation else None,
        city=city,
        country="NL" if country in DUTCH else country,
        posted_at=parse_date(posting.get("datePosted")),
        text=text,
        raw=redact(
            {
                "location": location,
                "validThrough": posting.get("validThrough"),
                "employmentType": posting.get("employmentType"),
                "page": url,
            }
        ),
    )


def identifier_of(posting: dict) -> str | None:
    found = posting.get("identifier")
    if isinstance(found, dict):
        found = found.get("value")
    if found is None:
        return None
    return str(found).strip() or None


def parse_date(value: Any) -> datetime | None:
    """ "2026-09-04", "2026-09-04T08:00:00+02:00" or "...Z": always aware (UTC
    when the site gives no zone), so it can be compared with now."""
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _hash(url: str) -> str:
    """A stable id for a posting that names none: the page it is on."""
    return hashlib.sha256(url.encode()).hexdigest()[:16]
