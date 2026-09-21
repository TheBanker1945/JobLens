"""What a fetch run did, and whether to believe it.

These sources fail quietly. Indeed answers an unchanged request with zero jobs
when its API moves. LinkedIn answers with pages whose descriptions never load.
A company simply posts nothing new for a week. On the surface all three look the
same: "nothing stored today".

So every search writes down what it did, and a handful of rules decide whether
the run as a whole is trustworthy. A search that finds nothing is normal; a
whole source that finds nothing, or a source whose jobs mostly arrive without a
description, is not. `scripts/fetch_vacancies.py` exits non-zero when the run is
unhealthy, which is what makes cron send the mail.
"""

import json
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path

# Statuses that mean the search did not finish, as opposed to finding nothing.
BROKEN = ("failed", "timeout", "throttled", "rate_limited")
MIN_SAMPLE = 10  # below this many jobs a share is noise, not a signal
MAX_EMPTY_SHARE = 0.3  # more descriptions missing than this: something is wrong


@dataclass
class SearchRun:
    """One source asked one question: "data engineer in Amsterdam"."""

    source: str
    search: str
    status: str = "ok"  # ok, empty, or one of BROKEN
    listed: int = 0  # jobs the board returned
    kept: int = 0  # of those, usable ones (a real description, an id and a url)
    dutch: int = 0  # of those, in the Netherlands
    stored: int = 0
    known: int = 0  # already stored under this source
    duplicate: int = 0  # the same job, already stored through another source
    dropped_no_text: int = 0
    dropped_invalid: int = 0
    detail: str = ""  # why it broke, in one line


@dataclass
class RunReport:
    started_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    searches: list[SearchRun] = field(default_factory=list)

    def add(self, run: SearchRun) -> SearchRun:
        self.searches.append(run)
        return run

    def problems(self) -> list[str]:
        """Everything about this run that deserves a human's attention."""
        problems = [
            f"{run.source} ({run.search}): {run.status}"
            + (f" - {run.detail}" if run.detail else "")
            for run in self.searches
            if run.status in BROKEN
        ]
        for source in dict.fromkeys(run.source for run in self.searches):
            finished = [
                run
                for run in self.searches
                if run.source == source and run.status not in BROKEN
            ]
            if not finished:
                continue
            listed = sum(run.listed for run in finished)
            if listed == 0:
                tried = (
                    "its only search"
                    if len(finished) == 1
                    else f"all {len(finished)} searches"
                )
                problems.append(
                    f"{source}: {tried} came back empty, which is also what "
                    "this source looks like when it breaks"
                )
                continue
            empty = sum(run.dropped_no_text for run in finished)
            if listed >= MIN_SAMPLE and empty / listed > MAX_EMPTY_SHARE:
                problems.append(
                    f"{source}: {empty} of {listed} jobs arrived without a "
                    "usable description"
                )
        return problems

    def healthy(self) -> bool:
        return not self.problems()

    def totals(self) -> dict[str, int]:
        fields = ("listed", "kept", "dutch", "stored", "known", "duplicate")
        return {
            name: sum(getattr(run, name) for run in self.searches) for name in fields
        }

    def as_dict(self) -> dict:
        return {
            "started_at": self.started_at.isoformat(timespec="seconds"),
            "finished_at": datetime.now(UTC).isoformat(timespec="seconds"),
            "healthy": self.healthy(),
            "problems": self.problems(),
            "totals": self.totals(),
            "searches": [asdict(run) for run in self.searches],
        }

    @staticmethod
    def latest(directory: Path) -> dict | None:
        """The most recent run's report, or None if there has never been one.

        Report names start with the date, so the newest name sorts last.
        """
        reports = sorted(directory.glob("*_fetch.json"))
        if not reports:
            return None
        return json.loads(reports[-1].read_text(encoding="utf-8"))

    def write(self, directory: Path) -> Path:
        """One file per run, so a bad week is visible next to a good one."""
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / f"{self.started_at:%Y-%m-%d_%H%M}_fetch.json"
        path.write_text(json.dumps(self.as_dict(), indent=2) + "\n", encoding="utf-8")
        return path
