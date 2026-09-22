"""Two runs of match_cv.py, side by side -- or the reason they cannot be.

    uv run python scripts/compare_runs.py                    # the two newest of one CV
    uv run python scripts/compare_runs.py --cv mahdi
    uv run python scripts/compare_runs.py --list
    uv run python scripts/compare_runs.py 2026-09-22_1442_mahdi 2026-09-22_1555_mahdi

The interesting question a week after a run is "did anything change, and was it
the corpus or was it me". Answering it needs the runs to be measurements of the
same thing, and `cv/runs.py` names the five ways they can fail to be: a different
CV, a different embedder, a different judge, a different prompt version, or a
different corpus to choose from. Any of those and this refuses, because
subtracting two numbers from different scales produces a number, and a number is
what people believe.

A corpus whose *contents* changed does not block: it is what happens every time
the scraper runs, and it is reported instead -- what entered the shortlist, what
left it, and how the vacancies present in both were judged this time.
"""

import argparse
import sys
from pathlib import Path

from joblens.cv.runs import Comparison, RunRecord, compare
from joblens.storage import FileStore, RunSummary

ROOT = Path(__file__).parent.parent


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("runs", nargs="*", help="two run ids, oldest first")
    parser.add_argument("--cv", help="only runs of this CV (the file stem)")
    parser.add_argument("--list", action="store_true", help="what has been stored")
    args = parser.parse_args()

    # A run is addressed by an id and not by a path: the store decides where it
    # keeps them, and the day that is a database this script does not change.
    store = FileStore(ROOT)
    stored = store.runs()
    if args.list:
        return show_list(stored)
    if len(args.runs) == 2:
        try:
            before, after = (store.load_run(run_id) for run_id in args.runs)
        except KeyError as err:
            print(f"{err}\nTry --list.")
            return 1
    elif args.runs:
        print("Give two run ids, or none and the two newest are used.")
        return 1
    else:
        pair = newest_pair(store, stored, args.cv)
        if pair is None:
            print(
                f"Need two runs of the same CV; found {len(stored)} run(s). "
                f"Run match_cv.py twice, or --list."
            )
            return 1
        before, after = pair

    print(f"before: {before.stamp.line()}")
    print(f"after:  {after.stamp.line()}\n")
    report(compare(before, after))
    return 0


def show_list(stored: list[RunSummary]) -> int:
    if not stored:
        print("No runs stored yet. match_cv.py writes one each time it judges.")
        return 1
    print(f"{len(stored)} run(s), newest first:")
    for one in stored:
        print(
            f"  {one.id:<30} {one.cv:<16} {one.judged:>2} judged of "
            f"{one.ranked:>3} ranked, {one.outcome}"
        )
    return 0


def newest_pair(
    store: FileStore, stored: list[RunSummary], cv: str | None
) -> tuple[RunRecord, RunRecord] | None:
    """The two most recent runs of one CV.

    Of *one* CV on purpose: the two newest files are most often two different
    people, and comparing those is the first thing `compare` would refuse
    anyway. Picking a pair that can be compared is friendlier than picking one
    that cannot and then explaining why.
    """
    if cv:
        stored = [one for one in stored if cv in one.cv]
    records = [store.load_run(one.id) for one in stored]
    by_cv: dict[str, list[RunRecord]] = {}
    for record in records:
        by_cv.setdefault(record.stamp.cv_name, []).append(record)
    pairs = [runs for runs in by_cv.values() if len(runs) >= 2]
    if not pairs:
        return None
    newest = max(pairs, key=lambda runs: max(one.stamp.at for one in runs))
    ordered = sorted(newest, key=lambda one: one.stamp.at)
    return ordered[-2], ordered[-1]


def report(result: Comparison) -> None:
    for note in result.notes:
        print(f"note: {note}")
    if not result.comparable:
        print("\nThese two runs are not comparable:")
        for blocker in result.blockers:
            print(f"  - {blocker}")
        print(
            "\nA verdict means something only inside one scale. Rather than show "
            "you\na difference that is really a change of measuring stick, this "
            "shows nothing."
        )
        return

    if result.notes:
        print()
    print(
        f"{len(result.entered)} entered the shortlist, {len(result.left)} left, "
        f"{len(result.changed)} judged differently, {result.unchanged} unchanged."
    )
    for row in sorted(result.entered, key=lambda r: -r.fit):
        print(f"  + [{row.verdict:<8} {row.fit:>3}] {row.title}")
    for row in sorted(result.left, key=lambda r: -r.fit):
        print(f"  - [{row.verdict:<8} {row.fit:>3}] {row.title}  (gone from the list)")
    for change in result.changed:
        print(
            f"  ~ {change.before} {change.fit_before} -> {change.after} "
            f"{change.fit_after}  {change.title}"
        )
    if result.changed:
        print(
            "\nA vacancy judged differently on an unchanged text is the judge "
            "being\nnon-deterministic, not the vacancy changing. Two runs of the "
            "same day are\nthe way to see how much of that there is."
        )


if __name__ == "__main__":
    sys.exit(main())
