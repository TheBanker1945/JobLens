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
   fetched on a night with a broken source are kept.

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
from pathlib import Path

import httpx

from joblens.cloud.state import BucketState, metadata_token
from joblens.embeddings.store import SQLiteVectors, cache_path
from joblens.storage import Database, published

ROOT = Path(__file__).parent.parent
PYTHON = sys.executable
STEPS = (
    ("fetch", [PYTHON, "scripts/fetch_vacancies.py"]),
    ("index", [PYTHON, "scripts/index_vacancies.py"]),
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
        print("The nightly update is switched off (settings, owner): nothing fetched.")
        database.close()
        return 0
    raw = ROOT / "data" / "raw"
    raw.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    with httpx.Client(timeout=120) as client:
        state = BucketState(
            bucket, raw, token=lambda: metadata_token(client), client=client
        )
        print(f"state: {state.download()} files from gs://{bucket}")
        model = os.environ.get("EMBED_MODEL", "")
        cache = SQLiteVectors(cache_path(ROOT / "data" / "cache", model))
        print(f"cache: {published.seed(cache, database, model)} published vectors")
        cache.close()
        database.close()

        failed = []
        # --no-fetch: only index and publish, to finish a night whose fetch
        # went fine without asking every site a second time the same day.
        steps = [
            step for step in STEPS if step[0] != "fetch" or "--no-fetch" not in sys.argv
        ]
        try:
            for name, command in steps:
                print(f"=== {name} ===")
                if subprocess.run(command, cwd=ROOT).returncode:
                    failed.append(name)
        finally:
            sent = state.upload()
            print(f"state: {len(sent)} changed files back to gs://{bucket}")
    minutes = (time.monotonic() - started) / 60
    if failed:
        print(f"done in {minutes:.0f} min; had a problem: {', '.join(failed)}")
        return 1
    print(f"done in {minutes:.0f} min")
    return 0


if __name__ == "__main__":
    sys.exit(main())
