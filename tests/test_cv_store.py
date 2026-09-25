"""The CV cache is shared by every script and, since phase 6, by worktrees."""

from joblens.cv.store import CVCache


def test_two_processes_writing_the_same_cache_keep_both_entries(tmp_path):
    """Each opened the file before the other wrote. Writing back the copy it
    read at startup made the second writer delete the first one's entry."""
    path = tmp_path / "cache.json"
    one, other = CVCache(path), CVCache(path)

    one.put("profile", "m", "cv one", {"headline": "one"})
    other.put("profile", "m", "cv two", {"headline": "two"})

    fresh = CVCache(path)
    assert fresh.get("profile", "m", "cv one") == {"headline": "one"}
    assert fresh.get("profile", "m", "cv two") == {"headline": "two"}


def test_a_write_leaves_no_temporary_file_behind(tmp_path):
    CVCache(tmp_path / "cache.json").put("profile", "m", "cv", {"headline": "x"})

    assert [p.name for p in tmp_path.iterdir()] == ["cache.json"]
