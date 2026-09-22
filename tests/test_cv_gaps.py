"""The aggregate gap: "what keeps coming up that you do not have".

The thing worth testing here is the grouping, because it is the only place in the
app where two different strings are declared to mean the same thing. Every test
below is a case where getting that wrong would put a false number on the screen:
merging what should stay apart, or leaving apart what a person reads as one thing.
"""

from joblens.cv.documents import QueryPart
from joblens.cv.gaps import summarise_gaps
from joblens.cv.judge import Judged, MatchJudgement
from joblens.cv.match import CVMatch
from joblens.cv.schema import CVProfile
from joblens.extraction.schema import VacancyDetails
from joblens.sources.base import Vacancy


def vacancy(number: int) -> Vacancy:
    return Vacancy(
        source="indeed",
        source_id=str(number),
        url=f"https://example.test/{number}",
        title=f"Data Engineer {number}",
        company="Acme",
        city="Utrecht",
        text="Wij vragen ervaring.",
    )


def details(*skills: str) -> VacancyDetails:
    return VacancyDetails(
        title="Data Engineer",
        company="Acme",
        city="Utrecht",
        work_mode=None,
        hours_min=None,
        hours_max=None,
        salary_min=None,
        salary_max=None,
        salary_period=None,
        salary_note=None,
        education_level=None,
        experience_years_min=None,
        skills=list(skills),
        languages_required=[],
        contract_type=None,
    )


def judged(number: int, fit: int, *gaps: tuple[str, str, bool]) -> Judged:
    verdict = "strong" if fit >= 75 else "possible" if fit >= 40 else "weak"
    judgement = MatchJudgement.model_validate(
        {
            "verdict": verdict,
            "fit": fit,
            "summary": "Een samenvatting.",
            "evidence": [],
            "gaps": [
                {"requirement": r, "vacancy_quote": q, "required": required}
                for r, q, required in gaps
            ],
        }
    )
    match = CVMatch(vacancy(number), 0.7, "document", QueryPart("the whole CV", "cv"))
    return Judged(match=match, judgement=judgement, dropped=[], quotes=len(gaps))


def profile(skills: list[str] | None = None, **stated) -> CVProfile:
    blank = {
        "headline": "Data-analist",
        "summary": None,
        "city": "Amsterdam",
        "experience": [],
        "education": [],
        "skills": skills or [],
        "certificates": [],
        "languages": ["Dutch"],
        "desired_work_mode": None,
        "availability": None,
    }
    return CVProfile.model_validate(blank | stated)


def test_a_requirement_named_by_several_vacancies_becomes_one_line():
    runs = [
        judged(1, 80, ("Ervaring met Azure", "je werkt met Azure", True)),
        judged(2, 60, ("Kennis van Azure", "ervaring met Azure is een pré", False)),
    ]
    summary = summarise_gaps(
        runs, profile(), {"indeed:1": details("Azure"), "indeed:2": details("Azure")}
    )
    assert [group.term for group in summary.groups] == ["Azure"]
    group = summary.groups[0]
    assert group.count == 2
    assert group.required_count == 1
    # fit/100 summed: the vacancy you nearly fit counts for more.
    assert group.weight == 1.4


def test_microsoft_azure_and_azure_are_one_requirement():
    """The brief's own example. Both spellings are skills some vacancy listed."""
    runs = [
        judged(1, 80, ("Microsoft Azure", "ervaring met Microsoft Azure", True)),
        judged(2, 70, ("Azure", "je werkt met Azure", True)),
    ]
    summary = summarise_gaps(
        runs,
        profile(),
        {"indeed:1": details("Microsoft Azure"), "indeed:2": details("Azure")},
    )
    assert len(summary.groups) == 1
    assert summary.groups[0].count == 2


def test_a_compound_is_not_merged_into_a_term_no_gap_raised():
    """ "Azure DevOps" stays itself when nothing else in the run asked for Azure.

    The merge only ever folds into a bucket the run produced on its own, so it
    cannot invent a heading that no vacancy's gap was about.
    """
    runs = [judged(1, 80, ("Azure DevOps", "werken met Azure DevOps", True))]
    summary = summarise_gaps(
        runs, profile(), {"indeed:1": details("Azure DevOps", "Azure")}
    )
    assert [group.term for group in summary.groups] == ["Azure DevOps"]


def test_a_skill_name_inside_a_longer_word_is_not_a_match():
    """ "SQL" must not be found inside "MySQL", or the count says something false."""
    runs = [judged(1, 80, ("MySQL beheren", "ervaring met MySQL", True))]
    summary = summarise_gaps(runs, profile(), {"indeed:1": details("SQL", "MySQL")})
    assert [group.term for group in summary.groups] == ["MySQL"]


def test_a_gap_naming_no_known_skill_is_counted_apart_and_not_guessed_at():
    runs = [judged(1, 80, ("Affiniteit met de zorgsector", "je voelt je thuis", False))]
    summary = summarise_gaps(runs, profile(), {"indeed:1": details("Python")})
    assert not summary.groups
    assert len(summary.ungrouped) == 1
    assert summary.grouped_share == 0.0


def test_a_gap_the_cv_already_covers_is_left_out_of_the_headline():
    """The headline is what you do NOT have, whatever the judge said."""
    runs = [judged(1, 80, ("Ervaring met Python", "je programmeert in Python", True))]
    summary = summarise_gaps(
        runs, profile(skills=["Python"]), {"indeed:1": details("Python")}
    )
    assert not summary.groups
    assert len(summary.already_on_cv) == 1


def test_groups_are_ordered_by_weight_not_by_how_often_they_appear():
    runs = [
        judged(1, 90, ("Databricks", "ervaring met Databricks", True)),
        judged(2, 20, ("Terraform", "kennis van Terraform", True)),
        judged(3, 20, ("Terraform", "kennis van Terraform", True)),
    ]
    summary = summarise_gaps(
        runs,
        profile(),
        {
            "indeed:1": details("Databricks"),
            "indeed:2": details("Terraform"),
            "indeed:3": details("Terraform"),
        },
    )
    assert [group.term for group in summary.groups] == ["Databricks", "Terraform"]
    assert summary.groups[0].count == 1 and summary.groups[1].count == 2


def test_the_field_checks_need_no_judge():
    """Education, language and years come from extracted fields, not from gaps."""
    # Revalidated rather than model_copy'd: `update` skips validation, and a
    # string where an enum belongs would make this test pass against code that
    # only works on strings.
    asking = VacancyDetails.model_validate(
        details("Python").model_dump()
        | {
            "education_level": "wo",
            "languages_required": ["German"],
            "experience_years_min": 8,
        }
    )
    runs = [judged(1, 80)]
    mine = profile(
        education=[
            {
                "programme": "Bedrijfskunde",
                "level": "hbo",
                "institution": "HvA",
                "end_year": 2020,
                "finished": True,
            }
        ],
        experience=[
            {
                "title": "Analist",
                "company": "Acme",
                "start": "2020",
                "end": "2024",
                "current": False,
                "summary": None,
                "skills": [],
            }
        ],
    )
    summary = summarise_gaps(runs, mine, {"indeed:1": asking})
    labels = {one.label for one in summary.fields}
    assert labels == {"opleidingsniveau", "taal", "jaren ervaring"}


def test_an_empty_run_summarises_to_nothing_rather_than_raising():
    summary = summarise_gaps([], profile(), {})
    assert summary.judged == 0
    assert not summary.groups and not summary.fields
    assert summary.grouped_share == 1.0


def test_one_vacancy_saying_a_thing_twice_counts_once():
    """ "in 2 of 10" has to mean two adverts, not one advert with two sections."""
    runs = [
        judged(
            1,
            80,
            ("Python", "je programmeert in Python", False),
            ("Python 3", "ervaring met Python is vereist", True),
        )
    ]
    summary = summarise_gaps(runs, profile(), {"indeed:1": details("Python")})
    group = summary.groups[0]
    assert group.count == 1
    assert group.weight == 0.8
    # The vacancy votes with its strongest wording: it does state it as a need.
    assert group.required_count == 1
    # But the grouping's own score still sees both gaps it was handed.
    assert summary.total_gaps == 2
    assert summary.grouped_share == 1.0
