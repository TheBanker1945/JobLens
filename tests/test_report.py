"""The health rules: which runs deserve a look, and which are simply quiet."""

import json

from joblens.sources.report import RunReport, SearchRun


def run(source="indeed", search="data engineer", **fields) -> SearchRun:
    return SearchRun(source=source, search=search, **fields)


def report_of(*runs) -> RunReport:
    report = RunReport()
    for one in runs:
        report.add(one)
    return report


def test_a_normal_run_is_healthy():
    report = report_of(run(listed=20, kept=20, dutch=20, stored=5, known=15))

    assert report.healthy()
    assert report.problems() == []


def test_one_empty_search_is_not_a_problem():
    """A search can simply have nothing new; only a dead source is a problem."""
    report = report_of(run(search="monteur", status="empty"), run(listed=12, kept=12))

    assert report.healthy()


def test_a_source_that_finds_nothing_at_all_is_a_problem():
    report = report_of(
        run(search="data engineer", status="empty"),
        run(search="monteur", status="empty"),
    )

    assert not report.healthy()
    assert "all 2 searches came back empty" in report.problems()[0]


def test_a_single_search_that_finds_nothing_reads_as_english():
    report = report_of(run(status="empty"))

    assert "its only search came back empty" in report.problems()[0]


def test_mostly_missing_descriptions_is_a_problem():
    report = report_of(run(source="linkedin", listed=20, kept=8, dropped_no_text=12))

    assert "12 of 20 jobs arrived without a usable description" in report.problems()[0]


def test_a_few_missing_descriptions_are_not():
    assert report_of(run(listed=20, kept=18, dropped_no_text=2)).healthy()


def test_shares_are_ignored_on_a_handful_of_jobs():
    assert report_of(run(listed=3, kept=1, dropped_no_text=2)).healthy()


def test_a_broken_search_is_reported_with_its_reason():
    report = report_of(
        run(status="throttled", detail="12 of 15 came back without a description"),
        run(search="monteur", listed=10, kept=10),
    )

    problems = report.problems()

    assert problems[0].startswith("indeed (data engineer): throttled - 12 of 15")
    assert len(problems) == 1  # the healthy search alongside it is not a problem


def test_a_source_that_only_broke_is_not_also_called_empty():
    report = report_of(run(status="rate_limited", detail="retry in 45 min"))

    assert len(report.problems()) == 1


def test_the_report_is_written_as_json(tmp_path):
    report = report_of(run(listed=20, kept=20, stored=20))

    path = report.write(tmp_path)
    written = json.loads(path.read_text(encoding="utf-8"))

    assert path.name.endswith("_fetch.json")
    assert written["healthy"] is True
    assert written["totals"]["stored"] == 20
    assert written["searches"][0]["source"] == "indeed"
    assert written["started_at"] and written["finished_at"]
