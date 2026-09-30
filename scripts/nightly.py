"""The nightly update in the cloud (7.8.5): fetch, index, publish, as a Cloud Run job.

    python scripts/nightly.py           # Cloud Scheduler, every night at 03:00
    python scripts/nightly.py --force   # a run by hand, even while switched off
    python scripts/nightly.py --force --no-fetch   # finish a night: index, publish

daily_update.sh on a laptop, moved to where it runs every night whether a
laptop is on or not (Mahdi, 2026-09-29: "everything should be cloud based",
Indeed through JobSpy included). A Cloud Run job starts with an empty disk,
so:

1. the vacancy state comes down from the bucket into data/raw/ (cloud/state.py:
   an allow-list, so nothing but vacancy state ever moves either way);
2. the embedding cache is seeded from what is published, so only new vacancies
   are embedded;
3. fetch_vacancies.py, index_vacancies.py and publish_corpus.py run exactly as
   on a laptop -- one failing does not stop the next, as in daily_update.sh;
4. what changed goes back up to the bucket, even when a step failed: vacancies
   fetched on a night with a broken source are kept;
5. what the run did goes into the database (nightly_runs), with every source
   and board as it left them (sources/overview.py): the owner's admin page
   reads it there, and never needs the bucket.

The exit code is non-zero when any step was, so a bad night shows as a failed
execution in the console. The job is never retried automatically: a retry
would ask every site again, which the politeness rules exist to prevent.

Settings: JOBLENS_STATE_BUCKET, DATABASE_URL, GEMINI_API_KEY and EMBED_* (as
for the app); the job's own service account reads the bucket.
"""

import os
import subprocess
import sys
import time
import traceback
from datetime import UTC, datetime
from pathlib import Path

import httpx

from joblens.cloud.state import BucketState, metadata_token
from joblens.embeddings.store import SQLiteVectors, cache_path
from joblens.sources.boards import load_config
from joblens.sources.overview import overview
from joblens.sources.report import RunReport
from joblens.storage import Database, published

ROOT = Path(__file__).parent.parent
PYTHON = sys.executable
STEPS = (
    ("fetch", [PYTHON, "scripts/fetch_vacancies.py"]),
    ("index", [PYTHON, "scripts/index_vacancies.py", "--open-only"]),
    ("publish", [PYTHON, "scripts/publish_corpus.py", "--to", "DATABASE_URL"]),
)


def main() -> int:
    sys.stdout.reconfigure(line_buffering=True)
    bucket = os.environ.get("JOBLENS_STATE_BUCKET")
    if not bucket:
        print("JOBLENS_STATE_BUCKET is not set: the bucket holding the vacancy state.")
        return 1
    database = Database.from_env()
    # The owner's switch in settings (7.8.5), off by default: Cloud Scheduler
    # starts this every night, and it asks no site at all while the switch is
    # off. --force runs it anyway, for a run started by hand.
    if "--force" not in sys.argv and not database.nightly_enabled():
        print("The nightly update is switched off (the admin page): nothing fetched.")
        database.close()
        return 0
    # One run at a time: Cloud Scheduler delivers a start at least once, and
    # was seen delivering one twice. Two runs would ask every site twice and
    # write the same files to the bucket.
    with database.nightly_lock() as mine:
        if not mine:
            print("Another nightly run is going: not starting a second one.")
            database.close()
            return 0
        fetch = "--no-fetch" not in sys.argv
        run_id = database.start_nightly(
            "hand" if "--force" in sys.argv else "schedule", fetch=fetch
        )
        return run(database, bucket, run_id, fetch=fetch)


def run(database: Database, bucket: str, run_id: int, *, fetch: bool = True) -> int:
    raw = ROOT / "data" / "raw"
    raw.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    # To the second, as a fetch report writes its start: a report from this
    # run is one that started at or after this.
    since = datetime.now(UTC).replace(microsecond=0)
    with httpx.Client(timeout=120) as client:
        state = BucketState(
            bucket, raw, token=lambda: metadata_token(client), client=client
        )
        print(f"state: {state.download()} files from gs://{bucket}")
        model = os.environ.get("EMBED_MODEL", "")
        cache = SQLiteVectors(cache_path(ROOT / "data" / "cache", model))
        print(f"cache: {published.seed(cache, database, model)} published vectors")
        cache.close()

        done: dict[str, bool] = {}
        # --no-fetch: only index and publish, to finish a night whose fetch
        # went fine without asking every site a second time the same day.
        steps = [step for step in STEPS if step[0] != "fetch" or fetch]
        try:
            for name, command in steps:
                print(f"=== {name} ===")
                done[name] = subprocess.run(command, cwd=ROOT).returncode == 0
        finally:
            try:
                sent = state.upload()
                print(f"state: {len(sent)} changed files back to gs://{bucket}")
            finally:
                record(database, run_id, done, since)
    failed = [name for name, went_well in done.items() if not went_well]
    minutes = (time.monotonic() - started) / 60
    if failed:
        print(f"done in {minutes:.0f} min; had a problem: {', '.join(failed)}")
        return 1
    print(f"done in {minutes:.0f} min")
    return 0


def record(
    database: Database, run_id: int, steps: dict[str, bool], since: datetime
) -> None:
    """What this run did, for the owner's admin page (7.10.1). A failure here
    is printed and never fails the run: the vacancies are what matters."""
    runs = ROOT / "data" / "raw" / "runs"
    report = RunReport.latest(runs) if "fetch" in steps and runs.exists() else None
    if report is not None and datetime.fromisoformat(report["started_at"]) < since:
        report = None  # the fetch broke before it wrote one: that one is older
    try:
        config = load_config(ROOT / "sources.toml", ROOT / "boards.toml")
        seen = overview(ROOT, config).model_dump(mode="json")
    except Exception:
        traceback.print_exc()
        seen = None
    try:
        database.finish_nightly(
            run_id,
            steps=steps,
            new_vacancies=report["totals"]["stored"] if report else None,
            problems=report["problems"] if report else [],
            overview=seen,
        )
    except Exception:
        traceback.print_exc()


if __name__ == "__main__":
    sys.exit(main())
