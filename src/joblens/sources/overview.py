"""What the owner's admin page shows about the vacancy sources (7.10.1).

The nightly job keeps the vacancy state in files (data/raw, from the bucket),
and the web app reads only Postgres. So at the end of every run the job reads
those files once, here, and stores the result with the run (nightly_runs):
the page never needs the bucket.

Per source, three numbers, each a part of the one before:

- **stored**: every vacancy it ever gave us (the store never forgets);
- **open**: of those, a real job that is still advertised (sightings.is_open);
- **in JobLens**: of those, what a match ranks -- extracted, and not the copy
  of a job already kept from another source. Exactly the set publish_corpus.py
  publishes, because it is counted from the same `load_corpus` call.

And from the last fetch that asked the source (sources/report.py): how many it
listed, how many were Dutch and inside the scope, how many were new, and
whether it went well. The last fetch *that asked it*, not the last fetch: a
`--source indeed` run says nothing about Greenhouse.

**Per board or search.** An employer board's vacancies are known by the board
that last listed them (sightings `board`), so a board has every number. A
search ("python developer in Utrecht") has only its last run: a vacancy two
searches found belongs to neither.
"""

from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

from pydantic import BaseModel

from joblens.corpus import is_vacancy, load_corpus
from joblens.sources.boards import BOARD_KEYS
from joblens.sources.polite import FetchState
from joblens.sources.report import BROKEN, stored_reports
from joblens.sources.sightings import Sightings
from joblens.sources.store import VacancyStore

# Sources whose vacancies are counted per board: the adapters that set a
# `board`. The government sitemap closes jobs like a board but is one listing,
# so it is one row.
PER_BOARD = {**BOARD_KEYS, "careersite": "site"}
SEARCH_SOURCES = ("overheid", "jobdataapi", "eures", "indeed", "linkedin")
LABEL = 30  # fetch_vacancies.py cuts a search's name to this in its report


class LastRun(BaseModel):
    """What the last fetch that asked a board or search found there. A
    source's is its searches added up: "ok", or "broken" and how many broke."""

    status: str = "ok"  # "ok", "empty", or one of report.BROKEN
    broken: int = 0  # a source's searches that broke
    listed: int = 0  # what the board or search returned
    in_scope: int = 0  # of those, Dutch and inside the scope: could be stored
    new: int = 0  # stored for the first time
    known: int = 0  # already stored
    duplicate: int = 0  # the same job, already stored from another source
    closed: int = 0  # stored jobs its board stopped listing
    detail: str = ""  # why it broke, in one line


class Row(BaseModel):
    """One board or search of a source."""

    name: str | None  # None: stored before sightings named a board
    stored: int | None = None  # None for a search: its vacancies are not its own
    open: int | None = None
    in_joblens: int | None = None
    run: LastRun | None = None  # None: the last fetch did not ask it


class SourceOverview(BaseModel):
    source: str
    enabled: bool  # sources.toml asks it; a disabled source keeps its vacancies
    per_board: bool  # rows are boards (with every number) or searches
    stored: int = 0
    open: int = 0
    in_joblens: int = 0
    fetched_at: datetime | None = None  # when the last fetch that asked it began
    run: LastRun | None = None
    problems: list[str] = []  # that fetch's problems with this source
    rows: list[Row] = []


class Refusal(BaseModel):
    """A site that refused us and is left alone until `until` (polite.py)."""

    site: str
    until: datetime
    reason: str
    strikes: int  # refusals in a row


class Overview(BaseModel):
    at: datetime
    sources: list[SourceOverview]
    refusing: list[Refusal]  # only those still waiting


def overview(root: Path, config: dict, now: datetime | None = None) -> Overview:
    """The sources as data/raw under `root` holds them now."""
    now = now or datetime.now(UTC)
    raw = root / "data" / "raw"
    store = VacancyStore(raw / "vacancies")
    sightings = Sightings.load(raw / "sightings.json")
    in_joblens = {
        vacancy.key
        for vacancy in load_corpus("raw", root, open_only=True).extracted().vacancies
    }
    last = last_asked(raw / "runs")
    enabled = enabled_sources(config)
    configured = {
        source: [entry[key] for entry in config.get(source, [])]
        for source, key in PER_BOARD.items()
    }

    sources = []
    for source in set(store.sources()) | enabled | set(last):
        vacancies = store.load(source)
        is_open = {
            v.key for v in vacancies if is_vacancy(v) and sightings.is_open(v, now)
        }
        seen = SourceOverview(
            source=source,
            enabled=source in enabled,
            per_board=source in PER_BOARD,
            stored=len(vacancies),
            open=len(is_open),
            in_joblens=sum(v.key in in_joblens for v in vacancies),
        )
        report, runs = last.get(source, (None, []))
        by_label = {run["search"]: last_run(run) for run in runs}
        if report is not None:
            seen.fetched_at = datetime.fromisoformat(report["started_at"])
            seen.run = added_up(runs)
            seen.problems = [p for p in report["problems"] if about(p, source)]
        if source in PER_BOARD:
            seen.rows = board_rows(
                {v.key: sighted(sightings, v.key) for v in vacancies},
                is_open=is_open,
                in_joblens=in_joblens,
                configured=configured[source],
                runs=by_label,
            )
        else:
            seen.rows = [Row(name=label, run=run) for label, run in by_label.items()]
        sources.append(seen)

    sources.sort(key=lambda one: (not one.enabled, -one.in_joblens, one.source))
    state = FetchState.load(raw / "fetch-state.json")
    refusing = [
        Refusal(
            site=site,
            until=entry.blocked_until,
            reason=entry.reason,
            strikes=entry.strikes,
        )
        for site, entry in sorted(state.sites.items())
        if entry.blocked_until > now
    ]
    return Overview(at=now, sources=sources, refusing=refusing)


def sighted(sightings: Sightings, key: str) -> str | None:
    """The board that last listed a vacancy; None before boards were noted."""
    entry = sightings.entries.get(key)
    return entry.board if entry else None


def board_rows(
    boards: dict[str, str | None],
    *,
    is_open: set[str],
    in_joblens: set[str],
    configured: list[str],
    runs: dict[str, LastRun],
) -> list[Row]:
    """One row per board: boards.toml's first, in its order, then any a report
    or the store knows that it no longer lists. A report cuts a board's name
    to LABEL characters, so that is what they are matched on. Vacancies with
    no board on record get a row of their own."""
    counted = {
        "stored": Counter(boards.values()),
        "open": Counter(boards[key] for key in is_open),
        "in_joblens": Counter(boards[key] for key in boards if key in in_joblens),
    }

    def numbers(name: str | None) -> dict[str, int]:
        return {what: counts[name] for what, counts in counted.items()}

    names = [*configured, *runs]
    names += [name for name in counted["stored"] if name is not None]
    full: dict[str, str] = {}
    for name in names:
        full.setdefault(name[:LABEL], name)
    rows = [
        Row(name=name, run=runs.get(label), **numbers(name))
        for label, name in full.items()
    ]
    if counted["stored"][None]:
        rows.append(Row(name=None, **numbers(None)))
    return rows


def last_asked(runs: Path) -> dict[str, tuple[dict, list[dict]]]:
    """Per source, the newest fetch report that asked it, and its searches
    there. Broken searches count: the page is where a breakage shows."""
    last: dict[str, tuple[dict, list[dict]]] = {}
    for report in stored_reports(runs) if runs.exists() else []:  # oldest first
        mine: dict[str, list[dict]] = {}
        for search in report["searches"]:
            mine.setdefault(search["source"], []).append(search)
        for source, searches in mine.items():
            last[source] = (report, searches)
    return last


def last_run(search: dict) -> LastRun:
    return LastRun(
        status=search["status"],
        listed=search["listed"],
        in_scope=search["dutch"] - search["out_of_scope"],
        new=search["stored"],
        known=search["known"],
        duplicate=search["duplicate"],
        closed=search["closed"],
        detail=search.get("detail", ""),
    )


def added_up(searches: list[dict]) -> LastRun:
    runs = [last_run(search) for search in searches]
    broken = sum(run.status in BROKEN for run in runs)
    total = LastRun(status="broken" if broken else "ok", broken=broken)
    for name in ("listed", "in_scope", "new", "known", "duplicate", "closed"):
        setattr(total, name, sum(getattr(run, name) for run in runs))
    return total


def about(problem: str, source: str) -> bool:
    """Whether a report's problem line is this source's: "indeed (3 searches):
    throttled", "indeed: all 22 searches came back empty"."""
    return problem.startswith((f"{source} (", f"{source}:"))


def enabled_sources(config: dict) -> set[str]:
    """The sources a scheduled fetch asks, read the way fetch_vacancies.py reads
    them: a company board when it lists a company, the rest when enabled."""
    boards = {name for name in PER_BOARD if config.get(name)}
    switched = {name for name in SEARCH_SOURCES if config.get(name, {}).get("enabled")}
    return boards | switched
