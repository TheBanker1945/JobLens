"""Stored runs, and the line between "compare these" and "refuse to".

The rule being tested: the five things that set the scale block a comparison, and
a corpus that has merely changed contents does not. Getting that backwards either
prints a difference between two different measuring sticks, or refuses every real
pair of runs a week apart.
"""

from joblens.cv.documents import QueryPart
from joblens.cv.judge import Judged, MatchJudgement
from joblens.cv.match import CVMatch
from joblens.cv.outcome import assess
from joblens.cv.runs import (
    RunStamp,
    build_record,
    compare,
    corpus_digest,
    list_runs,
    load_run,
    save_run,
)
from joblens.sources.base import Vacancy


def vacancy(number: int, title: str = "Data Engineer") -> Vacancy:
    return Vacancy(
        source="indeed",
        source_id=str(number),
        url=f"https://example.test/{number}",
        title=title,
        company="Acme",
        city="Utrecht",
        text="Wij vragen ervaring.",
    )


def judged(number: int, verdict: str, fit: int) -> Judged:
    judgement = MatchJudgement.model_validate(
        {
            "verdict": verdict,
            "fit": fit,
            "summary": "Een samenvatting.",
            "evidence": [],
            "gaps": [],
        }
    )
    match = CVMatch(vacancy(number), 0.7, "document", QueryPart("the whole CV", "cv"))
    return Judged(match=match, judgement=judgement, dropped=[], quotes=0)


def stamp(**stated) -> RunStamp:
    blank = {
        "cv_name": "mahdi",
        "cv_digest": "9f1c2b84",
        "corpus": "raw",
        "corpus_size": 279,
        "corpus_digest": "aaaaaaaa",
        "embed_model": "gemini-embedding-2",
        "judge_model": "gemini-3.8-flash",
        "cv_style": "raw",
        "prompt_version": "3.6",
        "top": 10,
    }
    return RunStamp.model_validate(blank | stated)


def record(runs: list[Judged], **stated):
    return build_record(stamp(**stated), runs, assess(runs, corpus=279))


def test_two_runs_of_the_same_scale_are_compared():
    before = record([judged(1, "possible", 60), judged(2, "weak", 20)])
    after = record([judged(1, "strong", 80), judged(3, "weak", 25)])
    result = compare(before, after)
    assert result.comparable
    assert [row.key for row in result.entered] == ["indeed:3"]
    assert [row.key for row in result.left] == ["indeed:2"]
    assert len(result.changed) == 1
    assert result.changed[0].before == "possible"
    assert result.changed[0].after == "strong"


def test_a_different_prompt_version_refuses():
    before = record([judged(1, "strong", 80)])
    after = record([judged(1, "weak", 20)], prompt_version="3.8")
    result = compare(before, after)
    assert not result.comparable
    assert any("judge prompt" in blocker for blocker in result.blockers)
    # Nothing is reported past the refusal: a diff would be read as a finding.
    assert not result.changed and not result.entered


def test_every_scale_field_blocks():
    before = record([judged(1, "strong", 80)])
    for field, value in [
        ("cv_digest", "deadbeef"),
        ("corpus", "samples"),
        ("embed_model", "qwen3-embedding:0.6b"),
        ("judge_model", "qwen3:8b"),
        ("prompt_version", "3.8"),
        ("cv_style", "profile"),
    ]:
        after = record([judged(1, "strong", 80)], **{field: value})
        assert not compare(before, after).comparable, field


def test_a_changed_corpus_is_a_note_and_not_a_blocker():
    """The corpus changes every time the scraper runs. Blocking on it would make
    this useless for the one question worth asking a week later."""
    before = record([judged(1, "strong", 80)], corpus_size=202, corpus_digest="1111")
    after = record([judged(1, "strong", 80)], corpus_size=279, corpus_digest="2222")
    result = compare(before, after)
    assert result.comparable
    assert any("not the same set of vacancies" in note for note in result.notes)


def test_the_same_size_with_different_contents_is_still_noticed():
    """279 and 279 can be two different 279s, which a count alone would hide."""
    assert corpus_digest([vacancy(1), vacancy(2)]) != corpus_digest(
        [vacancy(1), vacancy(3)]
    )
    # And the order the vacancies arrive in is not part of the identity.
    assert corpus_digest([vacancy(1), vacancy(2)]) == corpus_digest(
        [vacancy(2), vacancy(1)]
    )


def test_a_run_survives_a_round_trip_to_disk(tmp_path):
    original = record([judged(1, "strong", 80), judged(2, "weak", 20)])
    path = save_run(original, tmp_path)
    assert list_runs(tmp_path) == [path]
    assert load_run(path) == original
