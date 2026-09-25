"""Stored runs, and the line between "compare these" and "refuse to".

The rule being tested: the five things that set the scale block a comparison, and
a corpus that has merely changed contents does not. Getting that backwards either
prints a difference between two different measuring sticks, or refuses every real
pair of runs a week apart.
"""

import json

from joblens.corpus import Funnel
from joblens.cv.documents import QueryPart
from joblens.cv.judge import Evidence, Judged, MatchJudgement
from joblens.cv.match import CVMatch
from joblens.cv.outcome import assess
from joblens.cv.runs import (
    RunRecord,
    RunStamp,
    build_record,
    compare,
    corpus_digest,
)
from joblens.sources.base import Vacancy
from joblens.storage import FileStore


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
    store = FileStore(tmp_path)
    original = record([judged(1, "strong", 80), judged(2, "weak", 20)])

    run_id = store.save_run(original)

    assert [one.id for one in store.runs()] == [run_id]
    assert store.load_run(run_id) == original


def match(number: int, score: float) -> CVMatch:
    return CVMatch(vacancy(number), score, "document", QueryPart("the whole CV", "cv"))


def ranking(count: int) -> list[CVMatch]:
    """Five vacancies whose scores sit within two hundredths of each other."""
    return [match(n, 0.71 - n / 100) for n in range(1, count + 1)]


def test_the_whole_ranking_is_stored_and_says_which_ones_were_read():
    runs = [judged(1, "strong", 80), judged(2, "weak", 20)]

    stored = build_record(
        stamp(top=2),
        runs,
        assess(runs, corpus=5),
        ranking=ranking(5),
        shortlisted=2,
        funnel=Funnel(loaded=7, not_a_vacancy=1, duplicates=1),
    )

    assert [row.rank for row in stored.ranking] == [1, 2, 3, 4, 5]
    assert [row.judged for row in stored.ranking] == [True, True, False, False, False]
    # The point of the milestone: a vacancy nobody read still carries the number
    # it was rejected on, and the part of the CV it was compared against.
    rejected = stored.ranked_by_key()["indeed:5"]
    assert round(rejected.score, 3) == 0.66
    assert rejected.part == "the whole CV"
    assert stored.funnel.dropped == 2


def test_a_vacancy_whose_judge_call_failed_still_counts_as_read():
    """It was sent, it cost money, and it is not one of the never-shortlisted."""
    runs = [judged(1, "strong", 80)]

    stored = build_record(
        stamp(top=2),
        runs,
        assess(runs, corpus=5),
        ranking=ranking(5),
        shortlisted=2,
        failures=["indeed:2: the model returned nothing"],
    )

    assert stored.ranked_by_key()["indeed:2"].judged is True
    assert stored.by_key().keys() == {"indeed:1"}  # no judgement to show, though


def test_the_boundary_is_the_pair_either_side_of_the_cut():
    runs = [judged(1, "strong", 80)]
    stored = build_record(
        stamp(top=1), runs, assess(runs, corpus=5), ranking=ranking(5), shortlisted=1
    )

    last, first = stored.boundary()

    assert (last.rank, first.rank) == (1, 2)
    assert round(last.score - first.score, 3) == 0.01


def test_nothing_was_cut_off_when_every_vacancy_was_judged():
    runs = [judged(1, "strong", 80), judged(2, "weak", 20)]
    stored = build_record(
        stamp(top=2), runs, assess(runs, corpus=2), ranking=ranking(2), shortlisted=2
    )

    assert stored.boundary() is None


def test_a_run_stored_before_4_1_still_loads(tmp_path):
    """The five runs already in data/raw/cv-runs/ predate the ranking.

    They are the only "before" this milestone has, so they have to keep opening:
    a stored artifact that a later version cannot read is not an artifact.
    """
    store = FileStore(tmp_path)
    old = record([judged(1, "strong", 80)]).model_dump(mode="json")
    del old["ranking"], old["funnel"]
    path = store.runs_dir / "2026-09-22_1443_mahdi.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(old), encoding="utf-8")

    loaded = store.load_run("2026-09-22_1443_mahdi")

    assert loaded.ranking == []
    assert loaded.funnel.loaded == 0
    assert loaded.boundary() is None
    assert loaded.rows[0].fit == 80


def test_the_evidence_lines_are_stored_not_only_counted(tmp_path):
    """The viewer can only show "why it fits" if the run kept the lines."""
    one = judged(1, "strong", 85)
    one = Judged(
        match=one.match,
        judgement=one.judgement.model_copy(
            update={
                "evidence": [
                    Evidence(requirement="Python", cv_quote="Python 3.13, FastAPI")
                ]
            }
        ),
        dropped=[],
        quotes=1,
    )
    stored = record([one])

    row = stored.rows[0]
    assert row.evidence == 1
    assert [(c.requirement, c.quote) for c in row.claims] == [
        ("Python", "Python 3.13, FastAPI")
    ]
    path = tmp_path / "run.json"
    path.write_text(stored.model_dump_json(), encoding="utf-8")
    assert RunRecord.model_validate_json(path.read_text()).rows[0].claims


def test_a_capped_vacancy_is_stored_as_capped_and_not_as_the_cut():
    """With a cap the judged ones are not the head of the ranking, so they are
    named by key, and the cut is drawn below the last one judged."""
    runs = [judged(1, "strong", 80), judged(3, "weak", 20)]
    whole = ranking(5)

    stored = build_record(
        stamp(top=2, per_employer=1),
        runs,
        assess(runs, corpus=5),
        ranking=whole,
        sent={whole[0].vacancy.key, whole[2].vacancy.key},
        capped={whole[1].vacancy.key},
    )

    assert [(row.judged, row.capped) for row in stored.ranking[:4]] == [
        (True, False),
        (False, True),
        (True, False),
        (False, False),
    ]
    last, first = stored.boundary()
    assert (last.rank, first.rank) == (3, 4)


def test_a_run_from_before_the_cap_loads_with_no_cap():
    assert stamp().per_employer == 0
