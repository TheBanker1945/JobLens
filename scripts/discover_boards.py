"""Find the employer boards behind the vacancies we already have, and check them.

Indeed and jobdataapi copy vacancies from employers' own boards and keep a link
to where they copied from. This reads those links in data/raw/, names the boards
behind them (src/joblens/sources/boards.py), and checks each board JobLens does
not read yet with one request, through the same polite gate as a fetch: how
many jobs, how many Dutch, how many in the scope of sources.toml.

Nothing changes unless you say so. `--accept` adds the boards with work in scope
to boards.toml, which is committed; the next fetch reads them.

Usage:
    uv run python scripts/discover_boards.py              # check and print
    uv run python scripts/discover_boards.py --accept     # and add the useful ones
"""

import argparse
import json
import sys
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import httpx

from joblens.sources.boards import (
    Candidate,
    append_board,
    board_of,
    candidates,
    known_boards,
    load_boards,
    load_config,
)
from joblens.sources.greenhouse import GreenhouseSource
from joblens.sources.http import RateLimited, new_client
from joblens.sources.netherlands import is_dutch
from joblens.sources.polite import FetchState, Gate, PoliteTransport, Refused, Rules
from joblens.sources.recruitee import RecruiteeSource
from joblens.sources.scope import Scope
from joblens.sources.smartrecruiters import SmartRecruitersSource
from joblens.sources.store import VacancyStore
from joblens.sources.workday import WorkdaySource

ROOT = Path(__file__).parent.parent
RAW_DIR = ROOT / "data" / "raw" / "vacancies"
STATE_PATH = ROOT / "data" / "raw" / "fetch-state.json"


@dataclass
class Check:
    platform: str
    board: str
    links: int
    jobs: int | None = None
    dutch: int | None = None
    in_scope: int | None = None
    verdict: str = ""


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--config", type=Path, default=ROOT / "sources.toml")
    parser.add_argument("--boards", type=Path, default=ROOT / "boards.toml")
    parser.add_argument("--accept", action="store_true", help="add them to boards.toml")
    parser.add_argument(
        "--min-in-scope",
        type=int,
        default=1,
        help="accept a board with at least this many jobs in scope today",
    )
    parser.add_argument(
        "--platform",
        help="check only this platform's candidates (workday, recruitee, ...), "
        "so a new adapter does not re-ask every board turned down before",
    )
    args = parser.parse_args()

    config = load_config(args.config, args.boards)
    scope = Scope.from_config(config["scope"])
    markers = config.get("netherlands", {}).get("markers", [])
    store = VacancyStore(RAW_DIR)
    vacancies = [v for source in store.sources() for v in store.load(source)]
    known = known_boards(load_boards(args.boards))
    found, unreadable = candidates(vacancies, known)
    if args.platform:
        found = [c for c in found if c.platform == args.platform]
    print(
        f"links in {len(vacancies)} stored vacancies point at {len(found)} boards "
        "JobLens does not read yet; one request each to check them\n"
    )
    print(
        f"{'platform':<16} {'board':<28} {'links':>5} {'jobs':>5} {'dutch':>6} "
        f"{'scope':>6}  verdict"
    )

    gate = Gate(
        FetchState.load(STATE_PATH), Rules.from_config(config.get("politeness", {}))
    )
    checked: set[tuple[str, str]] = set()
    robots: dict = {}  # a Workday host's robots.txt, read once for all its sites
    checks = []
    with new_client(transport=PoliteTransport(gate)) as client:
        for candidate in found:
            one = check(candidate, client, scope, markers, known, checked, robots)
            one.verdict = one.verdict or verdict(one, args.min_in_scope)
            checks.append(one)
            print_row(one)
    gate.finish()

    print(f"\nrequests per site: {json.dumps(dict(gate.requests))}")
    if unreadable:
        platforms = ", ".join(f"{name} {n}" for name, n in unreadable.most_common())
        print(f"links to platforms with no adapter yet: {platforms}")

    accepted = [one for one in checks if one.verdict == "add"]
    if not args.accept:
        print(f"\n{len(accepted)} to add; run again with --accept to write them")
        return 0
    for one in accepted:
        note = (
            f"discovered {date.today()}: {one.jobs} jobs, {one.dutch} Dutch, "
            f"{one.in_scope} in scope ({one.links} stored links)"
        )
        append_board(args.boards, one.platform, one.board, note)
    print(f"\nadded {len(accepted)} boards to {args.boards.name}")
    return 0


def check(
    candidate: Candidate,
    client: httpx.Client,
    scope: Scope,
    markers: list[str],
    known: dict[str, set[str]],
    checked: set[tuple[str, str]],
    robots: dict | None = None,
) -> Check:
    """One board, one request (a short link: two). Never raises: a board that
    fails to answer is a row in the table, not the end of the run."""
    platform, board = candidate.platform, candidate.board
    one = Check(platform, board, candidate.links)
    try:
        if platform == "grnh.se":
            target = client.get(f"https://grnh.se/{board}").headers.get("location")
            resolved = board_of(target or "")
            if not resolved or resolved[0] != "greenhouse":
                one.verdict = "short link to the employer's own site: no board named"
                return one
            one.platform, one.board = platform, board = resolved
            if board in known["greenhouse"] or (platform, board) in checked:
                one.verdict = "a board already read or checked"
                return one
        checked.add((platform, board))
        if platform == "workday":
            # Five pages at most: enough to see whether a site has the work,
            # without reading all of a large one to find out.
            source = WorkdaySource(
                board, client, scope=scope, max_postings=100, robots=robots
            )
            postings = source.listing()
            one.jobs, one.dutch = source.stats.total, source.stats.dutch
            one.in_scope = sum(
                not source.not_wanted(p.get("title", ""), p.get("locationsText", ""))
                for p in postings
            )
            return one
        if platform == "smartrecruiters":
            postings = SmartRecruitersSource(board, client).listing()
            one.jobs = one.dutch = len(postings)  # the listing asks for NL only
            one.in_scope = sum(
                scope.check_listing(
                    p.get("name", ""), (p.get("location") or {}).get("fullLocation", "")
                ).keep
                for p in postings
            )
            return one
        source = (RecruiteeSource if platform == "recruitee" else GreenhouseSource)(
            board, client
        )
        jobs = source.fetch()
        dutch = [v for v in jobs if is_dutch(v, markers)]
        one.jobs, one.dutch = len(jobs), len(dutch)
        one.in_scope = sum(scope.keep(v) for v in dutch)
    except (Refused, RateLimited) as err:
        one.verdict = f"refused: {err}"
    except httpx.HTTPStatusError as err:
        one.verdict = f"not a {platform} board (HTTP {err.response.status_code})"
    except httpx.HTTPError as err:
        one.verdict = f"unreachable ({type(err).__name__})"
    except ValueError:  # an HTML page where JSON was expected
        one.verdict = f"not a {platform} board (no JSON)"
    return one


def verdict(one: Check, minimum: int) -> str:
    if one.in_scope is not None and one.in_scope >= minimum:
        return "add"
    return "no work in scope today"


def print_row(one: Check) -> None:
    def num(value: int | None) -> str:
        return "-" if value is None else str(value)

    print(
        f"{one.platform:<16} {one.board[:28]:<28} {one.links:>5} {num(one.jobs):>5} "
        f"{num(one.dutch):>6} {num(one.in_scope):>6}  {one.verdict}"
    )


if __name__ == "__main__":
    sys.exit(main())
