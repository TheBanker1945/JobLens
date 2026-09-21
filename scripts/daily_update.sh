#!/usr/bin/env bash
# One update: fetch new vacancies, then make them searchable. Meant for cron.
#
# Everything goes to a log file; nothing is printed unless something went wrong,
# because cron mails whatever a job prints. So: silence means it worked, and any
# mail is worth reading.
#
# Indexing runs even when fetching had a problem -- one broken source should not
# stop the vacancies that did arrive from becoming searchable -- but a problem in
# either step still makes this script exit non-zero.

set -uo pipefail
cd "$(dirname "$0")/.." || exit 1

mkdir -p data/raw/logs
log="data/raw/logs/$(date +%Y-%m-%d_%H%M).log"
status=0

{
    echo "=== fetch $(date --iso-8601=seconds) ==="
    uv run --group scrape python scripts/fetch_vacancies.py
} >>"$log" 2>&1 || status=1

{
    echo "=== index $(date --iso-8601=seconds) ==="
    uv run python scripts/index_vacancies.py
} >>"$log" 2>&1 || status=1

if [ "$status" -ne 0 ]; then
    echo "JobLens update had a problem. Last lines of $log:"
    tail -n 25 "$log"
fi

# Keep a month of logs and run reports; they are small, but not endless.
find data/raw/logs -name '*.log' -mtime +30 -delete 2>/dev/null
find data/raw/runs -name '*_fetch.json' -mtime +30 -delete 2>/dev/null

exit "$status"
