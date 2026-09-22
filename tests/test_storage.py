"""The one way in and out for runs, labels and preferences.

What is worth testing here is not that a file can be written. It is the three
decisions the seam makes: an id is not a path, a run never lands on top of
another run, and a real person's labels are private unless they already are not.
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


def test_a_run_is_addressed_by_an_id_and_listed_newest_first(tmp_path):
    store = FileStore(tmp_path)

    older = store.save_run(record(minute="2026-09-21T09:00:00"))
    newer = store.save_run(record(minute="2026-09-22T14:42:00"))

    assert [one.id for one in store.runs()] == [newer, older]
    assert store.runs()[0].cv == "mahdi"
    assert store.runs()[0].judged == 1
    assert store.load_run(older).stamp.cv_digest == "9f1c2b84"


def test_two_runs_in_the_same_minute_are_two_runs(tmp_path):
    """The old name was the minute alone, and a viewer will produce two."""
    store = FileStore(tmp_path)

    first = store.save_run(record())
    second = store.save_run(record())

    assert first != second
    assert {one.id for one in store.runs()} == {first, second}
    assert store.load_run(first).stamp.at == store.load_run(second).stamp.at


@pytest.mark.parametrize("bad", ["../secrets", "a/b", ".hidden", "..\\windows"])
def test_an_id_is_not_a_path(tmp_path, bad):
    """The web server in 4.3 will eventually be handed one of these."""
    with pytest.raises(KeyError):
        FileStore(tmp_path).load_run(bad)


def test_a_missing_run_is_a_key_error_and_not_an_empty_record(tmp_path):
    with pytest.raises(KeyError):
        FileStore(tmp_path).load_run("2026-09-22_1442_nobody")


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


def test_preferences_are_stored_without_a_schema(tmp_path):
    """4.5 decides what a preference is; the seam only has to keep one."""
    store = FileStore(tmp_path)

    store.save_preferences("mahdi", {"regio": ["Utrecht"], "minimum": 3500})

    assert store.load_preferences("mahdi") == {"regio": ["Utrecht"], "minimum": 3500}
    assert store.load_preferences("nobody") is None
