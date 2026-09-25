"""Keeping an uploaded CV: read it, redact it, profile it, store it (7.4).

The same three steps `match_cv.py` takes before it ranks anything -- read.py,
clean.py, extract.py through `prepare_cv` -- done once, at upload, and kept. A
match started later reads the stored text and profile (matching.stored_cv)
instead of the file, so it costs no model call and works after the file itself
has expired.

What is kept, and for how long (docs/web-app-phase-7.md):

- the redacted text, the profile and what was removed (counts): until the
  account is deleted;
- the uploaded file, unredacted: 30 days (storage KEEP_ORIGINAL), so that a
  better reader can read it again.

Only the redacted text is ever sent to a model, here as in the CLI.
"""

from contextlib import closing
from pathlib import Path

from joblens.cv.match import prepare_cv
from joblens.cv.read import CVFile
from joblens.cv.store import CVCache
from joblens.llm.client import LLMClient
from joblens.llm.structured import default_mode
from joblens.service.errors import CVUnreadable
from joblens.service.matching import PROFILES, ChatFactory, Models, provider_errors
from joblens.storage import CVRecord, CVStore

# Measured 2026-09-25: the real CV is 1.2 MB, and two of the ten strangers'
# CVs are 12.5 MB (photos embedded) and read fine, so 5 MB would refuse real
# CVs. 20 MB lets those in and still stops an upload being used to fill the
# database (Neon's free tier is 0.5 GB; files go after 30 days).
MAX_UPLOAD_BYTES = 20_000_000


def add_cv(
    store: CVStore,
    upload: CVFile,
    models: Models,
    *,
    cache_dir: Path,
    strip_name: str | None = None,
    chat: ChatFactory = LLMClient,
) -> CVRecord:
    """Read, redact and profile an upload, and keep it as the active CV.

    Costs one model call (about half a cent), or none when the same text was
    profiled before by the same model: the profile cache is keyed by the exact
    redacted text.
    """
    if len(upload.data) > MAX_UPLOAD_BYTES:
        raise CVUnreadable(
            f"{upload.name} is {len(upload.data) / 1e6:.1f} MB; a CV is at most "
            f"{MAX_UPLOAD_BYTES / 1e6:.0f} MB. Export it with smaller images."
        )
    with provider_errors(), closing(chat(models.cv)) as client:
        prepared = prepare_cv(
            upload,
            client,
            name=strip_name,
            model=models.cv.model,
            mode=default_mode(models.cv),
            cache=CVCache(cache_dir / PROFILES),
        )
    return store.add_cv(
        upload.name,
        prepared.text,
        profile=prepared.profile,
        strip_name=strip_name,
        original=upload.data,
        removed=prepared.redacted.counts(),
    )
