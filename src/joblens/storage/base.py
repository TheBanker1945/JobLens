"""What a store has to be able to do, and nothing about where it keeps it.

One interface for the three things phase 3 reads and writes -- runs, labels and
preferences -- so that everything above it (the scripts, the viewer, the API a
hosted version would wrap) talks to a `Store` and never to a directory.

**Why a seam before a database.** Today one person runs this on one laptop and
JSON files are the right answer: they are diffable, greppable, and a run is a
document rather than a table. A database earns its place when there is a second
user or a server that cannot share a filesystem, and by then the preferences
schema -- the only part of this with a genuinely unknown shape -- will have been
written against twenty real reasons instead of guessed at. What this seam buys
is that the swap is a day of implementing eight methods, rather than a week of
finding every `Path` in the repo.

**A store belongs to one person.** Nothing here takes an owner, because with one
person it would be a parameter that is always the same string, and an unused
field is a lie about what the code does. A second person is a directory level in
`FileStore` or a collection in a Firestore store: it changes the implementation
and not one caller, which is the whole point of the seam.

**7.2 is where the second person arrived**, and the seam held: `PostgresStore`
is built for one user (`Database.store_for(user_id)`), and every method below
answers for that user only. `FileStore` is still the one person on this
laptop, and the scripts and evals keep using it.

**CVs are the one new thing (`CVStore`).** Until the web app, a CV was a file a
script was pointed at. An upload has to be kept -- its redacted text, what a
model made of it, and for a while the file itself -- so the database store
keeps CVs too. `FileStore` does not: the CLI still reads CVs from disk.
"""

from datetime import datetime, timedelta
from typing import Protocol

from pydantic import BaseModel

from joblens.cv.outcome import Fit
from joblens.cv.read import CVFile
from joblens.cv.runs import RunRecord
from joblens.cv.schema import CVProfile
from joblens.evals.matching import CVLabels

# How long an uploaded file is kept after it was uploaded (Mahdi, 2026-09-25).
# Long enough that a better reader can read it again during the tester phase;
# the redacted text and the profile stay after the file is gone.
KEEP_ORIGINAL = timedelta(days=30)


class RunSummary(BaseModel):
    """A run as a list wants it: enough to choose one, not the run itself.

    A stored run is 70 KB now that it carries the whole ranking, so listing
    twenty of them should not mean loading 1.4 MB. A file store has to open each
    file anyway; a database would answer this from an index, and the interface is
    shaped for the second one so that arriving there changes nothing above.
    """

    id: str  # what `load_run` takes: opaque above this line
    cv: str
    at: datetime
    corpus: str
    corpus_size: int
    judged: int
    ranked: int
    outcome: Fit
    cost_usd: float | None = None

    @property
    def label(self) -> str:
        return f"{self.cv} · {self.at:%Y-%m-%d %H:%M} · {self.judged} judged"


class Store(Protocol):
    """Every read and write of a run, a label or a preference goes through this."""

    def runs(self) -> list[RunSummary]:
        """Newest first: a list of runs is nearly always read from the top."""
        ...

    def load_run(self, run_id: str) -> RunRecord: ...

    def save_run(self, record: RunRecord) -> str:
        """Returns the id it was stored under."""
        ...

    def labels(self) -> list[CVLabels]:
        """Every labelled CV, whoever judged it."""
        ...

    def load_labels(self, cv: str) -> CVLabels | None: ...

    def save_labels(self, labels: CVLabels) -> str: ...

    # What this person wants from their next job: one set per person, not per
    # CV (7.2) -- a CV is facts about the past, preferences constrain the
    # future. Stored as the mapping it is handed; 7.3 gives it a schema.
    def load_preferences(self) -> dict | None: ...

    def save_preferences(self, values: dict) -> str: ...


class User(BaseModel):
    """Someone with an account. Everything they store hangs off `id`."""

    id: str
    email: str | None = None  # null until 7.5 signs people in
    display_name: str | None = None
    locale: str | None = None  # en, nl, de, fr or es; None until chosen
    role: str = "tester"  # "owner" (unlimited) or "tester" (7.5, budget in 7.6)
    created_at: datetime

    @property
    def is_owner(self) -> bool:
        return self.role == "owner"


class CVRecord(BaseModel):
    """A CV as the app keeps it. The uploaded file is kept apart (`original`)."""

    id: str
    name: str  # the file stem: what runs and labels call this CV
    filename: str
    text: str  # redacted: the only version that is sent anywhere
    digest: str  # of `text`, as a run's stamp has it (cv/runs.py `digest`)
    profile: CVProfile | None = None  # what a model made of it
    strip_name: str | None = None  # the name redaction removed
    # How many of each kind of detail redaction took out ("email": 1). Counts,
    # never the values: those are what redaction exists to keep out.
    removed: dict[str, int] = {}
    uploaded_at: datetime
    active: bool


class CVStore(Protocol):
    """One active CV per person, and the ones they had before it."""

    def add_cv(
        self,
        filename: str,
        text: str,
        *,
        profile: CVProfile | None = None,
        strip_name: str | None = None,
        original: bytes | None = None,
        removed: dict[str, int] | None = None,
        at: datetime | None = None,
    ) -> CVRecord:
        """Store a CV as the active one; the one that was active becomes history.

        `original` is the uploaded file, kept for `KEEP_ORIGINAL` and then
        purged. `text` must already be redacted: a store keeps what it is
        given and does not check.
        """
        ...

    def cvs(self) -> list[CVRecord]:
        """Newest first, the active one among them."""
        ...

    def active_cv(self) -> CVRecord | None: ...

    def load_cv(self, cv_id: str) -> CVRecord: ...

    def activate_cv(self, cv_id: str) -> CVRecord:
        """Make an older CV the active one again."""
        ...

    def original(self, cv_id: str) -> CVFile | None:
        """The uploaded file, while it is kept; None once it has expired."""
        ...


class Job(BaseModel):
    """Something slow a request started and a page watches (7.4): a match."""

    id: str
    kind: str  # "match"
    status: str  # queued, running, done, failed
    stage: str | None = None  # reading, ranking, judging
    done: int = 0  # vacancies judged so far
    total: int = 0
    request: dict  # what was asked for
    run_id: str | None = None  # the stored run, once done
    error: str | None = None  # the sentence to show, once failed
    created_at: datetime
    updated_at: datetime

    @property
    def open(self) -> bool:
        return self.status in ("queued", "running")


class JobStore(Protocol):
    """One person's jobs. At most one open at a time: the database says so."""

    def start_job(self, kind: str, request: dict) -> Job:
        """Queue a job. ValueError when one is already open for this person."""
        ...

    def job(self, job_id: str) -> Job: ...

    def jobs(self, limit: int = 20) -> list[Job]:
        """Newest first."""
        ...


class ProviderKey(BaseModel):
    """A person's own model (7.6). `key_secret` is ciphertext (joblens/vault.py);
    only the service that makes a model call ever decrypts it."""

    provider: str  # one of llm/presets.py PRESETS
    model: str
    thinking: bool = False
    key_secret: bytes
    key_hint: str  # the last four characters, to say which key without showing it
    verified_at: datetime
