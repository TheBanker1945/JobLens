"""The work behind "start a match", run in the background (7.4).

A match takes 20 to 60 seconds, so a request does not do it: it queues a job
(storage `start_job`) and returns, and a worker thread calls `run_match_job`.
The job reads the account's active CV and preferences, ranks, judges, stores
the run, and writes its progress to the job row as it goes, so the page can
show "judging 4 of 10" by asking for the job.

**A job always ends.** Done with a run, or failed with a sentence a person can
act on. It never raises into the worker, and a server that stops half-way
leaves the job open only until the next start marks it interrupted
(Database.fail_interrupted_jobs).
"""

import logging
from pathlib import Path

from joblens.corpus import Corpus
from joblens.embeddings.client import EmbeddingClient
from joblens.llm.client import LLMClient
from joblens.preferences import Preferences
from joblens.service.errors import CVUnreadable, ServiceError
from joblens.service.matching import (
    ChatFactory,
    EmbedFactory,
    MatchRequest,
    Models,
    Progress,
    judge,
    rank,
)
from joblens.storage import Database

logger = logging.getLogger(__name__)

FAILED = "Something went wrong on our side and the match stopped. It has been logged."


def run_match_job(
    database: Database,
    user_id: str,
    job_id: str,
    *,
    corpus: Corpus,
    models: Models,
    cache_dir: Path,
    chat: ChatFactory = LLMClient,
    embed: EmbedFactory = EmbeddingClient,
) -> None:
    """Match the account's active CV, with its preferences, and store the run."""
    store = database.store_for(user_id)
    try:
        job = store.job(job_id)
        cv = store.active_cv()
        if cv is None:
            raise CVUnreadable("Upload a CV first: there is nothing to match.")
        answers = store.load_preferences()
        request = MatchRequest(
            cv=cv,
            top=job.request.get("top", 10),
            preferences=Preferences.model_validate(answers) if answers else None,
        )
        database.update_job(job_id, status="running")

        def progress(step: Progress) -> None:
            database.update_job(
                job_id, stage=step.stage, done=step.done, total=step.total
            )

        ranked = rank(
            request,
            corpus,
            models,
            cache_dir=cache_dir,
            progress=progress,
            chat=chat,
            embed=embed,
        )
        run = judge(
            ranked,
            corpus,
            models,
            cache_dir=cache_dir,
            store=store,
            progress=progress,
            chat=chat,
        )
        database.update_job(job_id, status="done", run_id=run.run_id)
    except ServiceError as err:
        database.update_job(job_id, status="failed", error=str(err))
    except Exception:  # noqa: BLE001 - a job must end, whatever broke
        logger.exception("match job %s failed", job_id)
        database.update_job(job_id, status="failed", error=FAILED)
