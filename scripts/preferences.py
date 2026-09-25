"""What you want from your next job: answer the questionnaire in the terminal.

    uv run python scripts/preferences.py ask     # answer, or change, the questions
    uv run python scripts/preferences.py show
    uv run python scripts/preferences.py set answers.json
    uv run python scripts/preferences.py clear
    ... --db you@example.com                      # an account's, not this laptop's

Then:
    uv run python scripts/match_cv.py <cv> --preferences   # a match that uses them
    uv run python scripts/eval_judge.py --labelled --cv mohammed --preferences

This is the web page's questionnaire (7.7) until there is a web page. Every
question can be left blank, and blank means "no preference", never "no".
Stored on this laptop in data/raw/preferences.json (never committed), or in the
database with --db.

How they are used (docs/learning-log.md, 7.3): contract, hours, work mode,
distance, salary, languages, seniority and employers move a vacancy that states
something else behind the ones that do not -- never out of the list, and never
for something it does not state. Years, degree and sectors are told to the
judge, because they need reading rather than a field.
"""

import argparse
import sys
from collections.abc import Callable
from pathlib import Path

from dotenv import load_dotenv
from pydantic import ValidationError

from joblens.extraction.schema import ContractType, WorkMode
from joblens.preferences import Geo, Preferences, Seniority
from joblens.preferences.schema import ANY_YEARS
from joblens.storage import Database, FileStore

ROOT = Path(__file__).parent.parent
CLEAR = "-"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("command", choices=["ask", "show", "set", "clear"])
    parser.add_argument("file", nargs="?", type=Path, help="for set: a JSON file")
    parser.add_argument("--db", metavar="EMAIL", help="an account in the database")
    args = parser.parse_args()

    store = open_store(args.db)
    if store is None:
        return 1
    current = Preferences.model_validate(store.load_preferences() or {})

    if args.command == "show":
        show(current)
        return 0
    if args.command == "clear":
        store.save_preferences(Preferences().model_dump(mode="json"))
        print("cleared: no preferences, so nothing moves.")
        return 0
    if args.command == "set":
        if not args.file:
            parser.error("set needs a JSON file")
        try:
            answers = Preferences.model_validate_json(args.file.read_text("utf-8"))
        except ValidationError as err:
            print(f"Not saved: {err}")
            return 1
    else:
        try:
            answers = ask(current)
        except (KeyboardInterrupt, EOFError):
            print("\nstopped: nothing saved.")
            return 1
    store.save_preferences(answers.model_dump(mode="json"))
    print()
    show(answers)
    print("\nsaved.")
    return 0


def open_store(email: str | None):
    if not email:
        return FileStore(ROOT)
    load_dotenv()
    database = Database.from_env()
    user = database.user_by_email(email)
    if user is None:
        print(f"No account for {email}. scripts/db.py create-user makes one.")
        return None
    return database.store_for(user.id)


def ask(current: Preferences) -> Preferences:
    print(
        "Enter keeps the answer in [brackets], '-' clears it. Blank means no "
        "preference.\n"
    )
    values = current.model_dump()
    values["contract_types"] = choose(
        "Which contracts do you want?",
        ContractType,
        current.contract_types,
    )
    while True:
        values["hours_min"] = number("Fewest hours a week you want", current.hours_min)
        values["hours_max"] = number("Most hours a week you want", current.hours_max)
        low, high = values["hours_min"], values["hours_max"]
        if low is None or high is None or low <= high:
            break
        print("  the fewest is more than the most; again")
    values["work_modes"] = choose(
        "Where do you want to work?", WorkMode, current.work_modes
    )
    values["home"] = place("Where do you live? (a town or city)", current.home)
    values["max_distance_km"] = (
        number("At most how many km away, as the crow flies", current.max_distance_km)
        if values["home"]
        else None
    )
    values["salary_min"] = number(
        "Lowest salary you want, gross euro a month", current.salary_min
    )
    values["seniority"] = choose(
        "Which levels do you want?", Seniority, current.seniority
    )
    values["languages"] = words(
        "Which languages do you work in? (Dutch, English, ...)", current.languages
    )
    values["avoid_employers"] = words(
        "Employers you do not want to work for", current.avoid_employers
    )
    values["avoid_sectors"] = words(
        "Sectors you do not want to work in", current.avoid_sectors
    )
    values["stretch_years"] = years(current.stretch_years)
    values["stretch_degree"] = yes_no(
        "If a vacancy asks a degree level (mbo/hbo/wo) your CV does not show, "
        "do you still apply?",
        current.stretch_degree,
    )
    return Preferences.model_validate(values)


def answer(question: str, shown: str) -> str | None:
    """None: keep. "": clear. Anything else: the new answer."""
    typed = input(f"{question} [{shown or 'no preference'}]: ").strip()
    if not typed:
        return None
    return "" if typed == CLEAR else typed


def choose(question: str, options, current: list) -> list:
    names = [one.value for one in options]
    while True:
        typed = answer(
            f"{question} ({', '.join(names)}; comma-separated)",
            ", ".join(one.value for one in current),
        )
        if typed is None:
            return list(current)
        chosen = [one.strip().lower() for one in typed.split(",") if one.strip()]
        unknown = [one for one in chosen if one not in names]
        if not unknown:
            return [options(one) for one in chosen]
        print(f"  not an option: {', '.join(unknown)}")


def number(question: str, current: int | None) -> int | None:
    while True:
        typed = answer(question, "" if current is None else str(current))
        if typed is None:
            return current
        if typed == "":
            return None
        if typed.isdigit():
            return int(typed)
        print("  a whole number, please")


def place(question: str, current: str | None) -> str | None:
    geo = Geo.load()
    while True:
        typed = answer(question, current or "")
        if typed is None:
            return current
        if typed == "" or geo.knows(typed):
            return typed or None
        print(f"  {typed!r} is not a Dutch place JobLens can find on the map")


def words(question: str, current: list[str]) -> list[str]:
    typed = answer(f"{question} (comma-separated)", ", ".join(current))
    if typed is None:
        return list(current)
    return [one.strip() for one in typed.split(",") if one.strip()]


def years(current: int | None) -> int | None:
    shown = {None: "", 0: "no", ANY_YEARS: "any"}.get(current, str(current))
    while True:
        typed = answer(
            "If a vacancy asks more years of experience than your CV shows, do "
            "you still apply? (no, 1, 2, 3 = up to that many more, any)",
            shown,
        )
        if typed is None:
            return current
        if typed == "":
            return None
        if typed.lower() in ("no", "0"):
            return 0
        if typed.lower() == "any":
            return ANY_YEARS
        if typed.isdigit() and 1 <= int(typed) < ANY_YEARS:
            return int(typed)
        print("  no, a number of years, or any")


def yes_no(question: str, current: bool | None) -> bool | None:
    shown = {None: "", True: "yes", False: "no"}[current]
    while True:
        typed = answer(f"{question} (yes, no)", shown)
        if typed is None:
            return current
        if typed == "":
            return None
        if typed.lower() in ("yes", "y", "ja", "j"):
            return True
        if typed.lower() in ("no", "n", "nee"):
            return False
        print("  yes or no")


def show(preferences: Preferences) -> None:
    if preferences.is_empty():
        print("No preferences: nothing moves, and the judge is told nothing.")
        return
    lines: list[tuple[str, Callable[[], str]]] = [
        ("contract", lambda: ", ".join(t.value for t in preferences.contract_types)),
        ("hours a week", lambda: _span(preferences.hours_min, preferences.hours_max)),
        ("work mode", lambda: ", ".join(m.value for m in preferences.work_modes)),
        ("home", lambda: preferences.home or ""),
        (
            "distance",
            lambda: (
                f"at most {preferences.max_distance_km} km"
                if preferences.max_distance_km
                else ""
            ),
        ),
        (
            "salary",
            lambda: (
                f"at least €{preferences.salary_min:,} a month"
                if preferences.salary_min is not None
                else ""
            ),
        ),
        ("level", lambda: ", ".join(s.value for s in preferences.seniority)),
        ("languages", lambda: ", ".join(preferences.languages)),
        ("avoid employers", lambda: ", ".join(preferences.avoid_employers)),
        ("avoid sectors", lambda: ", ".join(preferences.avoid_sectors)),
        ("more years", lambda: _years(preferences.stretch_years)),
        (
            "higher degree",
            lambda: {None: "", True: "apply anyway", False: "do not apply"}[
                preferences.stretch_degree
            ],
        ),
    ]
    for label, value in lines:
        print(f"  {label:<16} {value() or '-'}")
    print(f"  (stamped on runs as {preferences.stamp()})")


def _span(low: int | None, high: int | None) -> str:
    if low is None and high is None:
        return ""
    return f"{low if low is not None else 0}-{high if high is not None else '...'}"


def _years(value: int | None) -> str:
    if value is None:
        return ""
    if value == 0:
        return "do not apply"
    return "apply anyway" if value >= ANY_YEARS else f"apply up to {value} more"


if __name__ == "__main__":
    sys.exit(main())
