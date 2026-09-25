"""A gap that names a choice of skills is not a gap in any one of them.

"AWS, GCP or Azure" used to be counted as missing Azure, the longest name in
it, so the summary could say a skill keeps coming up that no vacancy required.
"""

import pytest
from test_cv_gaps import details, judged, profile

from joblens.cv.gaps import summarise_gaps

SKILLS = ("AWS", "GCP", "Azure", "Docker", "Kubernetes", "CI/CD", "Terraform")


def summary(requirement: str):
    """The same requirement in two vacancies, so a group would count it twice."""
    runs = [judged(n, 60, (requirement, requirement, True)) for n in (1, 2)]
    corpus = {one.match.vacancy.key: details(*SKILLS) for one in runs}
    return summarise_gaps(runs, profile(["Python"]), corpus)


@pytest.mark.parametrize(
    "requirement",
    [
        "Experience with AWS, GCP or Azure",
        "Ervaring met Docker of Kubernetes",  # Dutch "of" is "or"
        "Container tooling (Docker/Kubernetes)",
    ],
)
def test_a_choice_of_skills_is_counted_under_none_of_them(requirement):
    result = summary(requirement)

    assert result.groups == []
    assert len(result.ungrouped) == 2  # still shown, as could not be grouped


@pytest.mark.parametrize(
    ("requirement", "term"),
    [
        ("Docker and Kubernetes in production", "Kubernetes"),
        ("Knowledge of Docker and Kubernetes", "Kubernetes"),  # "of" before the list
        ("CI/CD pipelines and Terraform", "Terraform"),  # the "/" is inside a skill
        ("Hands-on Azure experience", "Azure"),  # one skill is no choice
    ],
)
def test_a_list_that_is_not_a_choice_is_grouped_as_before(requirement, term):
    result = summary(requirement)

    assert [group.term for group in result.groups] == [term]
    assert result.groups[0].count == 2
