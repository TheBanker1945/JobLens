"""FileStore's own behaviour: where it puts things on disk, and why.

What every store promises is in test_store_contract.py, run on this one and on
Postgres alike.

What is left here is FileStore's own decision: a real person's labels are
private unless they already are not, and a save that fails leaves the old file.
"""

import json

import pytest

from joblens.cv.documents import QueryPart
from joblens.cv.judge import Judged, MatchJudgement
from joblens.cv.match import CVMatch
from joblens.cv.outcome import assess
from joblens.cv.runs import RunStamp, build_record
from joblens.evals.matching import CVLabels
from joblens.sources.base import Vacancy
from joblens.storage import FileStore


def judged(number: int = 1, verdict: str = "strong", fit: int = 80) -> Judged:
    vacancy = Vacancy(
        source="indeed",
        source_id=str(number),
        url="https://example.test",
        title="Data Engineer",
        text="Wij vragen ervaring.",
    )
    judgement = MatchJudgement.model_validate(
        {
            "verdict": verdict,
            "fit": fit,
            "summary": "Een samenvatting.",
            "evidence": [],
            "gaps": [],
        }
    )
    match = CVMatch(vacancy, 0.7, "document", QueryPart("the whole CV", "cv"))
    return Judged(match=match, judgement=judgement, dropped=[], quotes=0)


def record(cv: str = "mahdi", minute: str = "2026-09-22T14:42:00"):
    runs = [judged()]
    stamp = RunStamp.model_validate(
        {
            "cv_name": cv,
            "cv_digest": "9f1c2b84",
            "corpus": "raw",
            "corpus_size": 279,
            "corpus_digest": "aaaaaaaa",
            "embed_model": "gemini-embedding-2",
            "judge_model": "gemini-3.8-flash",
            "cv_style": "raw",
            "prompt_version": "3.6",
            "top": 10,
            "at": minute,
        }
    )
    return build_record(stamp, runs, assess(runs, corpus=279))


def test_labels_for_a_new_cv_are_private(tmp_path):
    """The safe direction: a real person's labels never default into the repo."""
    store = FileStore(tmp_path)

    written = store.save_labels(CVLabels(cv="mahdi", corpus="raw", judged_by="Mahdi"))

    assert store.private_labels_dir in store.labels_path("mahdi").parents
    assert "cv-labels" in written
    assert not store.shared_labels_dir.exists()


def test_labels_that_are_already_committed_keep_being_written_there(tmp_path):
    """The four invented CVs are the repo's evidence and stay updatable."""
    store = FileStore(tmp_path)
    store.shared_labels_dir.mkdir(parents=True)
    (store.shared_labels_dir / "lisa_de_vries.json").write_text(
        json.dumps({"cv": "lisa_de_vries", "corpus": "raw", "judged_by": "Claude"}),
        encoding="utf-8",
    )

    store.save_labels(
        CVLabels(cv="lisa_de_vries", corpus="raw", judged_by="Claude", relevant=["a:1"])
    )

    assert store.load_labels("lisa_de_vries").relevant == ["a:1"]
    assert not (store.private_labels_dir / "lisa_de_vries.json").exists()


def test_both_label_directories_are_read_as_one(tmp_path):
    store = FileStore(tmp_path)
    store.shared_labels_dir.mkdir(parents=True)
    (store.shared_labels_dir / "lisa_de_vries.json").write_text(
        json.dumps({"cv": "lisa_de_vries", "corpus": "raw", "judged_by": "Claude"}),
        encoding="utf-8",
    )
    store.save_labels(CVLabels(cv="mahdi", corpus="raw", judged_by="Mahdi"))

    assert [one.cv for one in store.labels()] == ["lisa_de_vries", "mahdi"]
    assert store.load_labels("nobody") is None


def test_a_failed_save_leaves_the_labels_that_were_there(tmp_path, monkeypatch):
    """Writing in place empties the file first; a crash then loses every reason."""
    import os

    import joblens.storage.files as files
    from joblens.evals.matching import CVLabels

    store = FileStore(tmp_path)
    store.save_labels(CVLabels(cv="mahdi", corpus="raw", judged_by="Mahdi", note="v1"))

    def broken(*args):
        raise OSError("disk full")

    monkeypatch.setattr(files.os, "replace", broken)
    with pytest.raises(OSError):
        store.save_labels(
            CVLabels(cv="mahdi", corpus="raw", judged_by="Mahdi", note="v2")
        )
    monkeypatch.setattr(files.os, "replace", os.replace)

    assert store.load_labels("mahdi").note == "v1"
    assert not list(tmp_path.rglob("*.tmp"))


def test_only_committed_labels_count_as_shared(tmp_path):
    from joblens.evals.matching import CVLabels

    store = FileStore(tmp_path)
    store.shared_labels_dir.mkdir(parents=True)
    (store.shared_labels_dir / "lisa.json").write_text(
        CVLabels(cv="lisa", corpus="raw", judged_by="Claude").model_dump_json()
    )
    store.save_labels(CVLabels(cv="mahdi", corpus="raw", judged_by="Mahdi"))

    assert store.is_shared_labels("lisa")
    assert not store.is_shared_labels("mahdi")
