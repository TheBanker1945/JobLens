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
"""

from datetime import datetime
from typing import Protocol

from pydantic import BaseModel

from joblens.cv.outcome import Fit
from joblens.cv.runs import RunRecord
from joblens.evals.matching import CVLabels


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

    # Preferences have no schema yet, on purpose: milestone 4.5 writes one from
    # real reasons rather than from a guess at a form, and until then the store
    # persists whatever mapping it is handed. The seam exists so that the day
    # there is a schema, there is already one place that reads and writes it.
    def load_preferences(self, cv: str) -> dict | None: ...

    def save_preferences(self, cv: str, values: dict) -> str: ...
