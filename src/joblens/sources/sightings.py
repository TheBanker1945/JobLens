"""When each stored vacancy was last seen, and whether it has closed.

The store is append-only and knows nothing about time: a vacancy fetched on
20 September was still "there" a month later, and the matcher would happily
recommend it. Measured 2026-09-22: two of two employer-site vacancies fetched
two days earlier already answered 404.

Two kinds of source, two ways of knowing:

- **A board lists every job it has** (Recruitee, Greenhouse, SmartRecruiters).
  So a stored job that a board no longer lists has closed -- exactly, and on
  the night it went. Only after a *successful* fetch of that board, and never
  on an empty one: a board that suddenly lists nothing is more likely broken
  than emptied, and closing everything on it would be the worst guess.
- **A search lists what matches this week** (Indeed, jobdataapi, LinkedIn). A
  job missing from tonight's search may simply be older than the search window,
  so absence proves nothing. Such a job is open while a search still lists it
  (seen in the last `SEARCH_SEEN_DAYS`) or while it is young (at most
  `SEARCH_MAX_AGE_DAYS` since posting; a Dutch vacancy typically runs four to
  six weeks). Age alone was the first rule, and the data broke it: Indeed
  returned six jobs in a last-7-days search whose `date_posted` was 183 to 334
  days back (2026-09-23). A job Indeed keeps re-listing is still advertised.

Nothing is ever deleted. data/raw/sightings.json holds the dates; the corpus
asks `is_open` when the app ranks, and the evals keep reading everything, so a
closed vacancy their labels refer to does not move their scores.
"""

import json
import os
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from joblens.sources.base import Vacancy

# Sources whose listing is complete, so a job missing from it has closed. The
# government sitemap (5.5) lists every vacancy, so it closes jobs like a board;
# a Workday listing does too, unless it stopped short (see workday.py).
BOARD_SOURCES = (
    "recruitee",
    "greenhouse",
    "smartrecruiters",
    "overheid",
    "workday",
)
SEARCH_MAX_AGE_DAYS = 30  # since posting
SEARCH_SEEN_DAYS = 7  # since a search last listed it: Indeed's own window


@dataclass
class Sighting:
    first_seen: datetime
    last_seen: datetime
    board: str | None = None  # "channable": which listing it was seen on
    closed_at: datetime | None = None


class Sightings:
    """data/raw/sightings.json: one entry per vacancy key that a fetch saw."""

    def __init__(self, path: Path | None = None):
        self.path = path  # None: in memory only (tests)
        self.entries: dict[str, Sighting] = {}
        self.tonight: set[str] = set()  # every key `seen` during this run

    @classmethod
    def load(cls, path: Path) -> "Sightings":
        sightings = cls(path)
        if path.exists():
            for key, raw in json.loads(path.read_text(encoding="utf-8")).items():
                sightings.entries[key] = Sighting(
                    first_seen=datetime.fromisoformat(raw["first_seen"]),
                    last_seen=datetime.fromisoformat(raw["last_seen"]),
                    board=raw.get("board"),
                    closed_at=_date(raw.get("closed_at")),
                )
        return sightings

    def save(self) -> None:
        """Written whole, to a temporary file first, then moved into place: a run
        that dies halfway leaves yesterday's file, never half of today's."""
        if self.path is None:
            return
        data = {
            key: {
                "first_seen": s.first_seen.isoformat(timespec="seconds"),
                "last_seen": s.last_seen.isoformat(timespec="seconds"),
                "board": s.board,
                "closed_at": s.closed_at.isoformat(timespec="seconds")
                if s.closed_at
                else None,
            }
            for key, s in sorted(self.entries.items())
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(json.dumps(data, indent=1) + "\n", encoding="utf-8")
        os.replace(temporary, self.path)

    def seen(self, keys: set[str], board: str | None, now: datetime) -> int:
        """These keys were listed just now. Returns how many had been closed and
        are back: a board that takes a job down and puts it up again."""
        reopened = 0
        self.tonight |= keys
        for key in keys:
            entry = self.entries.get(key)
            if entry is None:
                self.entries[key] = Sighting(now, now, board)
                continue
            entry.last_seen = now
            entry.board = board or entry.board
            if entry.closed_at is not None:
                entry.closed_at = None
                reopened += 1
        return reopened

    def seen_elsewhere(self, keys: set[str], now: datetime) -> None:
        """Another source listed these stored jobs tonight, under its own key.

        The store keeps the first copy of a job it meets and drops the rest, so
        Wildflowers' "Full-Stack Java Developer" is stored as an Indeed copy, and
        its own board's listing never is (2026-09-23). Without this, the Indeed
        copy ages out while the employer still lists the job. `last_seen` moves;
        `closed_at` never does: a job its own board has closed stays closed,
        whatever a copy elsewhere says.
        """
        for key in keys:
            entry = self.entries.get(key)
            if entry is None:
                self.entries[key] = Sighting(now, now)
            else:
                entry.last_seen = now

    def close_missing(self, board: str, source: str, listed: set[str], now) -> int:
        """A board was fetched successfully: its open jobs that it no longer
        lists have closed. Returns how many closed just now."""
        if not listed:  # an empty board is more likely broken than emptied
            return 0
        closed = 0
        for key, entry in self.entries.items():
            if (
                entry.board == board
                and key.startswith(f"{source}:")
                and entry.closed_at is None
                and key not in listed
            ):
                entry.closed_at = now
                closed += 1
        return closed

    def close_unseen(self, source: str, stored: set[str], now: datetime) -> int:
        """Every board of `source` was fetched successfully tonight: a stored job
        none of them listed has closed, even one stored before sightings began
        (it has no board on record, so no single board could close it). A board
        taken out of boards.toml closes this way too: nothing reads it any more,
        so nothing can say its jobs are still open."""
        seen = {key for key in self.tonight if key.startswith(f"{source}:")}
        if not seen:  # every board of the source came back empty: closes nothing
            return 0
        closed = 0
        for key in stored - seen:
            entry = self.entries.get(key)
            if entry is None:
                self.entries[key] = Sighting(now, now, None, closed_at=now)
                closed += 1
            elif entry.closed_at is None:
                entry.closed_at = now
                closed += 1
        return closed

    def is_open(self, vacancy: Vacancy, now: datetime | None = None) -> bool:
        now = now or datetime.now(UTC)
        entry = self.entries.get(vacancy.key)
        if vacancy.source in BOARD_SOURCES:
            return entry is None or entry.closed_at is None
        if entry is not None and now - entry.last_seen <= timedelta(
            days=SEARCH_SEEN_DAYS
        ):
            return True
        return age(vacancy, now) <= timedelta(days=SEARCH_MAX_AGE_DAYS)


def age(vacancy: Vacancy, now: datetime) -> timedelta:
    """Since the employer posted it, or else since we first fetched it."""
    when = vacancy.posted_at or vacancy.fetched_at
    if when.tzinfo is None:
        when = when.replace(tzinfo=UTC)
    return now - when


def _date(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value else None
