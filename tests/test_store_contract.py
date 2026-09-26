"""What every store promises, run against each of them.

A seam is only a seam if both sides keep the same promises. Each test here runs
once on `FileStore` (the files on this laptop) and once on `PostgresStore` (the
database the web app uses); a behaviour that holds for one and not the other
fails here rather than in front of a user. What only one store does -- the two
label directories of FileStore, the CVs and accounts of Postgres -- is tested
in test_storage.py and test_postgres.py.
"""

from datetime import datetime

import pytest
from test_storage import record

from joblens.evals.matching import Call, CVLabels, Decision
from joblens.storage import FileStore


@pytest.fixture(params=["files", "postgres"])
def store(request, tmp_path):
    if request.param == "files":
        return FileStore(tmp_path)
    database = request.getfixturevalue("database")
    return database.store_for(database.create_user(email="lisa@example.test").id)


def test_a_run_is_addressed_by_an_id_and_listed_newest_first(store):
    older = store.save_run(record(minute="2026-09-21T09:00:00"))
    newer = store.save_run(record(minute="2026-09-22T14:42:00"))

    assert [one.id for one in store.runs()] == [newer, older]
    assert store.runs()[0].cv == "mahdi"
    assert store.runs()[0].judged == 1
    assert store.load_run(older).stamp.cv_digest == "9f1c2b84"


def test_a_stored_run_comes_back_exactly_as_it_was_saved(store):
    saved = record(minute="2026-09-22T14:42:17.123456")

    loaded = store.load_run(store.save_run(saved))

    assert loaded == saved
    assert store.runs()[0].at == datetime(2026, 9, 22, 14, 42, 17, 123456)


def test_two_runs_in_the_same_minute_are_two_runs(store):
    """The old name was the minute alone, and a viewer will produce two."""
    first = store.save_run(record())
    second = store.save_run(record())
    third = store.save_run(record())

    assert len({first, second, third}) == 3
    # Newest first, and within the minute by id, the same way in every store.
    assert [one.id for one in store.runs()] == sorted(
        [first, second, third], reverse=True
    )


@pytest.mark.parametrize("bad", ["../secrets", "a/b", ".hidden", "..\\windows"])
def test_an_id_is_not_a_path(store, bad):
    """The web server in 4.3 will eventually be handed one of these."""
    with pytest.raises(KeyError):
        store.load_run(bad)


def test_a_missing_run_is_a_key_error_and_not_an_empty_record(store):
    with pytest.raises(KeyError):
        store.load_run("2026-09-22_1442_nobody")


def test_labels_keep_every_reason_word_for_word(store):
    labels = CVLabels(cv="mahdi", corpus="raw", judged_by="Mahdi")
    labels.record(
        Decision(
            key="indeed:1",
            call=Call.NO,
            reason="Te ver: 1u45 met het ov, en 'hybride' betekent hier 4 dagen.",
        )
    )

    store.save_labels(labels)
    labels.record(Decision(key="indeed:2", call=Call.APPLY, reason="Precies dit."))
    store.save_labels(labels)  # the second save replaces the first

    loaded = store.load_labels("mahdi")
    assert loaded == labels
    assert loaded.decision_for("indeed:1").reason.startswith("Te ver: 1u45")
    assert [one.cv for one in store.labels()] == ["mahdi"]
    assert store.load_labels("nobody") is None


def test_preferences_are_one_set_per_person_without_a_schema_yet(store):
    """7.3 decides what a preference is; the seam only has to keep one."""
    assert store.load_preferences() is None

    store.save_preferences({"regio": ["Utrecht"], "minimum": 3500})
    store.save_preferences({"regio": ["Utrecht", "Leiden"], "minimum": 3600})

    assert store.load_preferences() == {"regio": ["Utrecht", "Leiden"], "minimum": 3600}
