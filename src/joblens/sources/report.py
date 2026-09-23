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
import re
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path

# Statuses that mean the search did not finish, as opposed to finding nothing.
# The last three come from the gate in sources/polite.py: a site that refused us,
# one we did not ask because it refused us recently, and a run that reached the
# per-site request budget.
BROKEN = (
    "failed",
    "timeout",
    "throttled",
    "rate_limited",
    "blocked",
    "cooling_down",
    "over_budget",
)
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
    # Dutch jobs that --limit left out. A choice rather than a fault, so it is
    # not a problem below -- but a cap that bites looks exactly like a quiet
    # board unless it is counted (see sources/netherlands.py).
    capped: int = 0
    # Dutch jobs outside the scope (sources/scope.py): counted, and named in
    # `left_out`, so a scope that is too narrow can be seen and widened.
    out_of_scope: int = 0
    stored: int = 0
    known: int = 0  # already stored under this source
    duplicate: int = 0  # the same job, already stored through another source
    dropped_no_text: int = 0
    dropped_invalid: int = 0
    detail: str = ""  # why it broke, in one line
    left_out: list[str] = field(default_factory=list)  # "title -- why", at most 25


@dataclass
class RunReport:
    started_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    searches: list[SearchRun] = field(default_factory=list)
    # Requests per site, as the gate counted them. The scraped sources count one
    # per search: JobSpy sends its own requests, and we only see the search.
    requests: dict[str, int] = field(default_factory=dict)

    def add(self, run: SearchRun) -> SearchRun:
        self.searches.append(run)
        return run

    def problems(self) -> list[str]:
        """Everything about this run that deserves a human's attention.

        Searches that broke the same way are one line: a site that refused us
        makes every later search of that source stop for the same reason, and
        twelve identical lines in a cron mail hide the one that is different.
        """
        broken: dict[tuple[str, str, str], list[str]] = {}
        for run in self.searches:
            if run.status in BROKEN:
                key = (run.source, run.status, run.detail)
                broken.setdefault(key, []).append(run.search)
        problems = []
        for (source, status, detail), searches in broken.items():
            which = searches[0] if len(searches) == 1 else f"{len(searches)} searches"
            reason = f" - {detail}" if detail else ""
            problems.append(f"{source} ({which}): {status}{reason}")
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
        fields = (
            "listed",
            "kept",
            "dutch",
            "out_of_scope",
            "capped",
            "stored",
            "known",
            "duplicate",
        )
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
            "requests": dict(sorted(self.requests.items())),
            "searches": [asdict(run) for run in self.searches],
        }

    @staticmethod
    def latest(directory: Path) -> dict | None:
        """The most recent run's report, or None if there has never been one."""
        reports = stored_reports(directory)
        return reports[-1] if reports else None

    @staticmethod
    def last_fetched(directory: Path) -> dict[str, datetime]:
        """When each source last finished a search, over every stored report.

        The newest report alone cannot answer this. `fetch_vacancies.py
        --source indeed` writes a report that is perfectly healthy and says
        nothing about the other sources -- which is how two days without a
        Greenhouse fetch read as "up to date" on 2026-09-22.

        A search that broke does not count: nothing was fetched. One that came
        back empty does, because the board was asked and answered.
        """
        last: dict[str, datetime] = {}
        for report in stored_reports(directory):  # oldest first
            # The start, not the finish: a run takes minutes, and the earlier
            # of the two can only make a source look older, never fresher.
            started = datetime.fromisoformat(report["started_at"])
            for search in report["searches"]:
                if search["status"] not in BROKEN:
                    last[search["source"]] = started
        return last

    def write(self, directory: Path) -> Path:
        """One file per run, so a bad week is visible next to a good one.

        Never on top of another run. Three `--source` runs in one minute on
        2026-09-22 left a single report, and the two it replaced were the only
        record that Greenhouse and Recruitee had been fetched at all. The same
        rule as `FileStore.save_run`: a second run in a minute gets `-2`.
        """
        directory.mkdir(parents=True, exist_ok=True)
        stem = f"{self.started_at:%Y-%m-%d_%H%M}"
        path, attempt = directory / f"{stem}_fetch.json", 2
        while path.exists():
            path, attempt = directory / f"{stem}-{attempt}_fetch.json", attempt + 1
        path.write_text(json.dumps(self.as_dict(), indent=2) + "\n", encoding="utf-8")
        return path


def stored_reports(directory: Path) -> list[dict]:
    """Every report in `directory`, oldest first.

    Ordered by when each run started, not by file name: "..._2000-2_fetch.json"
    sorts *before* "..._2000_fetch.json", because "-" comes before "_". Two runs
    that started in the same second are ordered by their suffix, which is the
    order they were written in.
    """
    loaded = [
        (json.loads(path.read_text(encoding="utf-8")), _attempt(path))
        for path in directory.glob("*_fetch.json")
    ]
    loaded.sort(key=lambda one: (datetime.fromisoformat(one[0]["started_at"]), one[1]))
    return [report for report, _ in loaded]


def _attempt(path: Path) -> int:
    """1 for "2026-09-22_2000_fetch.json", 2 for "2026-09-22_2000-2_fetch.json"."""
    found = re.search(r"-(\d+)$", path.name.removesuffix("_fetch.json"))
    return int(found.group(1)) if found else 1
