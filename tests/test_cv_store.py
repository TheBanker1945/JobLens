"""The CV cache is written by more than one process at a time."""

import json

from joblens.cv.store import CVCache


def test_two_writers_keep_each_others_entries(tmp_path):
    """`match_cv.py` and an eval open the file at the same moment; the one that
    writes last must not delete what the other one added."""
    path = tmp_path / "cache.json"
    first, second = CVCache(path), CVCache(path)

    first.put("profile", "m", "cv one", {"n": 1})
    second.put("profile", "m", "cv two", {"n": 2})

    reread = CVCache(path)
    assert reread.get("profile", "m", "cv one") == {"n": 1}
    assert reread.get("profile", "m", "cv two") == {"n": 2}


def test_a_write_leaves_whole_json_and_no_temporary_file(tmp_path):
    path = tmp_path / "cache.json"
    cache = CVCache(path)

    cache.put("judgement-3.6", "m", "pair", {"verdict": "weak"})

    assert len(json.loads(path.read_text(encoding="utf-8"))) == 1
    assert [p.name for p in tmp_path.iterdir()] == ["cache.json"]


def test_the_newest_write_of_one_entry_wins(tmp_path):
    path = tmp_path / "cache.json"
    stale, fresh = CVCache(path), CVCache(path)
    stale.put("profile", "m", "cv", {"n": "old"})
    fresh.put("profile", "m", "cv", {"n": "new"})

    assert CVCache(path).get("profile", "m", "cv") == {"n": "new"}
