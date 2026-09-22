"""The store, backed by JSON files, which is where all of this already lived.

Three directories, and the interesting one is the labels:

    data/raw/cv-runs/      runs -- a CV and real vacancy text, so never committed
    data/raw/cv-labels/    labels for a real CV -- ditto
    evals/cv-matches/      labels for the four invented CVs -- committed evidence
    data/raw/preferences/  whatever 4.5 decides a preference is

**Labels live in two places because they are two different things.** The sample
CVs' labels are part of the repo's evidence: someone who clones this can run the
eval and get the same numbers. A real person's labels say which real jobs that
person would apply to, which is personal data and belongs under data/raw/ with
the CV it describes. Reading merges both, and writing follows the file that
already exists -- so a CV nobody has labelled before is private by default, which
is the safe direction to be wrong in.
"""

import json
from pathlib import Path

from joblens.cv.runs import RunRecord
from joblens.evals.matching import CVLabels
from joblens.storage.base import RunSummary


class FileStore:
    def __init__(self, root: Path):
        self.root = root
        self.runs_dir = root / "data" / "raw" / "cv-runs"
        self.private_labels_dir = root / "data" / "raw" / "cv-labels"
        self.shared_labels_dir = root / "evals" / "cv-matches"
        self.preferences_dir = root / "data" / "raw" / "preferences"

    # -- runs ---------------------------------------------------------------

    def runs(self) -> list[RunSummary]:
        return sorted(
            (self._summarise(path) for path in self.runs_dir.glob("*.json")),
            # By id as well as by time: two runs of the same minute would
            # otherwise come back in whatever order the filesystem listed them.
            key=lambda one: (one.at, one.id),
            reverse=True,
        )

    def load_run(self, run_id: str) -> RunRecord:
        path = self._run_path(run_id)
        if not path.exists():
            raise KeyError(f"no run {run_id!r} in {self.runs_dir}")
        return RunRecord.model_validate_json(path.read_text(encoding="utf-8"))

    def save_run(self, record: RunRecord) -> str:
        """Stored under a minute-stamped id, and never on top of another run.

        Two runs a minute apart are already in data/raw/, and a viewer that can
        start one makes two in the same minute likely. Losing the earlier one
        would be losing the "before" of whatever the second one was testing.
        """
        self.runs_dir.mkdir(parents=True, exist_ok=True)
        stem = f"{record.stamp.at:%Y-%m-%d_%H%M}_{record.stamp.cv_name}"
        run_id, attempt = stem, 2
        while self._run_path(run_id).exists():
            run_id, attempt = f"{stem}-{attempt}", attempt + 1
        self._run_path(run_id).write_text(
            json.dumps(record.model_dump(mode="json"), indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        return run_id

    def path_of(self, run_id: str) -> Path:
        """Where a run is on disk. For printing a path a person can open."""
        return self._run_path(run_id)

    # -- labels -------------------------------------------------------------

    def labels(self) -> list[CVLabels]:
        found: dict[str, CVLabels] = {}
        for directory in (self.shared_labels_dir, self.private_labels_dir):
            for path in sorted(directory.glob("*.json")):
                labels = CVLabels.model_validate_json(path.read_text(encoding="utf-8"))
                found[labels.cv] = labels  # private wins: it is the newer copy
        return sorted(found.values(), key=lambda one: one.cv)

    def load_labels(self, cv: str) -> CVLabels | None:
        return next((one for one in self.labels() if one.cv == cv), None)

    def save_labels(self, labels: CVLabels) -> str:
        path = self.labels_path(labels.cv)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            # mode="json" because a decision carries a timestamp, and a store
            # that can only write the models it was written for is not a seam.
            json.dumps(labels.model_dump(mode="json"), indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        return str(path)

    def labels_path(self, cv: str) -> Path:
        """Where this CV's labels are written: beside the ones that exist.

        A CV nobody has labelled is private. Only a CV whose labels are already
        in the committed directory -- the four invented ones -- keeps writing
        there, so the repo's evidence stays updatable and a real person's labels
        can never drift into it by default.
        """
        shared = self.shared_labels_dir / f"{cv}.json"
        return shared if shared.exists() else self.private_labels_dir / f"{cv}.json"

    # -- preferences --------------------------------------------------------

    def load_preferences(self, cv: str) -> dict | None:
        path = self.preferences_dir / f"{cv}.json"
        if not path.exists():
            return None
        return json.loads(path.read_text(encoding="utf-8"))

    def save_preferences(self, cv: str, values: dict) -> str:
        self.preferences_dir.mkdir(parents=True, exist_ok=True)
        path = self.preferences_dir / f"{cv}.json"
        path.write_text(
            json.dumps(values, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        return str(path)

    # -----------------------------------------------------------------------

    def _run_path(self, run_id: str) -> Path:
        # An id is a file stem and nothing else: a store is not a filesystem to
        # whatever is above it, and "../../etc/passwd" is a request the web
        # server in 4.3 will eventually be handed.
        if "/" in run_id or "\\" in run_id or run_id.startswith("."):
            raise KeyError(f"not a run id: {run_id!r}")
        return self.runs_dir / f"{run_id}.json"

    def _summarise(self, path: Path) -> RunSummary:
        record = RunRecord.model_validate_json(path.read_text(encoding="utf-8"))
        return RunSummary(
            id=path.stem,
            cv=record.stamp.cv_name,
            at=record.stamp.at,
            corpus=record.stamp.corpus,
            corpus_size=record.stamp.corpus_size,
            judged=len(record.rows),
            ranked=len(record.ranking),
            outcome=record.outcome,
            cost_usd=record.cost_usd,
        )
