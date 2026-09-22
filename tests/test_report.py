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


def test_a_capped_run_is_healthy_and_says_how_many_it_left_out(tmp_path):
    """--limit is a choice, not a fault; it is counted so it cannot hide."""
    report = report_of(
        run(source="greenhouse", listed=212, kept=212, dutch=56, capped=6)
    )

    written = json.loads(report.write(tmp_path).read_text(encoding="utf-8"))

    assert report.healthy()
    assert written["totals"]["capped"] == 6
    assert written["searches"][0]["capped"] == 6


def test_the_report_is_written_as_json(tmp_path):
    report = report_of(run(listed=20, kept=20, stored=20))

    path = report.write(tmp_path)
    written = json.loads(path.read_text(encoding="utf-8"))

    assert path.name.endswith("_fetch.json")
    assert written["healthy"] is True
    assert written["totals"]["stored"] == 20
    assert written["searches"][0]["source"] == "indeed"
    assert written["started_at"] and written["finished_at"]


def test_the_latest_report_is_the_newest_one(tmp_path):
    from datetime import UTC, datetime

    older = RunReport(started_at=datetime(2026, 9, 19, 7, 15, tzinfo=UTC))
    newer = RunReport(started_at=datetime(2026, 9, 20, 7, 15, tzinfo=UTC))
    newer.add(run(stored=7))
    older.write(tmp_path)
    newer.write(tmp_path)

    assert RunReport.latest(tmp_path)["totals"]["stored"] == 7


def test_no_report_yet_is_not_an_error(tmp_path):
    assert RunReport.latest(tmp_path) is None


def test_a_run_of_one_source_does_not_refresh_the_others(tmp_path):
    """2026-09-22: two Indeed-only runs made two-day-old Greenhouse data read as
    'up to date', because only the newest report was consulted."""
    from datetime import UTC, datetime

    full = RunReport(started_at=datetime(2026, 9, 20, 16, 10, tzinfo=UTC))
    full.add(run(source="greenhouse", search="adyen", listed=212))
    full.add(run(source="indeed", listed=40))
    full.write(tmp_path)
    indeed_only = RunReport(started_at=datetime(2026, 9, 22, 12, 7, tzinfo=UTC))
    indeed_only.add(run(source="indeed", listed=30))
    indeed_only.write(tmp_path)

    last = RunReport.last_fetched(tmp_path)

    assert last["greenhouse"].date().isoformat() == "2026-09-20"
    assert last["indeed"] > last["greenhouse"]


def test_a_search_that_broke_is_not_a_fetch_but_an_empty_one_is(tmp_path):
    from datetime import UTC, datetime

    report = RunReport(started_at=datetime(2026, 9, 22, 7, 15, tzinfo=UTC))
    report.add(run(source="jobdataapi", status="rate_limited"))
    report.add(run(source="recruitee", search="nmbrs", status="empty"))
    report.write(tmp_path)

    last = RunReport.last_fetched(tmp_path)

    assert "jobdataapi" not in last
    assert "recruitee" in last
