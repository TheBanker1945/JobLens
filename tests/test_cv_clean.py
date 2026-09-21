"""What leaves the machine when a CV is read, and what does not."""

import pytest

from joblens.cv.clean import redact_cv
from joblens.sources.clean import EMAIL, PHONE

CV = """Lisa de Vries
Keizersgracht 412, 1016 GC Amsterdam
lisa.devries@example.com | 06-24871903
linkedin.com/in/lisadevries | https://github.com/lisadv
Geboortedatum: 14-06-1998
Nederlandse nationaliteit

Data-analist bij Coolblue, Rotterdam, maart 2022 - heden
Salaris 3200 - 3800 per maand, 32 uur per week
Python 3.11, Power BI, dbt
"""


@pytest.mark.parametrize(
    "secret",
    [
        "lisa.devries@example.com",
        "06-24871903",
        "Keizersgracht 412",
        "1016 GC",
        "14-06-1998",
        "linkedin.com/in/lisadevries",
        "github.com/lisadv",
    ],
)
def test_personal_details_do_not_survive(secret):
    assert secret not in redact_cv(CV).text


@pytest.mark.parametrize(
    "kept",
    [
        "Amsterdam",  # the city decides whether a job is commutable
        "Rotterdam",
        "Nederlandse nationaliteit",  # can be a hard requirement in a vacancy
        "3200 - 3800",  # a salary range is not a phone number
        "32 uur",
        "Python 3.11",  # nor is a version number
        "maart 2022 - heden",
    ],
)
def test_what_matching_needs_is_kept(kept):
    assert kept in redact_cv(CV).text


def test_nothing_that_looks_like_contact_details_is_left():
    """The belt-and-braces check: whatever the rules did, nothing matches after."""
    text = (
        redact_cv(CV).text.replace("[phone removed]", "").replace("[email removed]", "")
    )

    assert not EMAIL.search(text)
    assert not PHONE.search(text)


def test_removals_are_reported_with_what_was_removed():
    redacted = redact_cv(CV)

    assert redacted.counts()["email"] == 1
    assert redacted.counts()["url"] == 2
    assert any(r.original == "06-24871903" for r in redacted.removals)


def test_name_is_kept_unless_one_is_given():
    assert "Lisa de Vries" in redact_cv(CV).text
    assert "Lisa" not in redact_cv(CV, name="Lisa de Vries").text


def test_a_name_is_removed_everywhere_it_appears():
    text = "Jan Willem de Boer\nReferentie: vraag naar de Boer\nWillem werkte hier."

    redacted = redact_cv(text, name="Jan Willem de Boer")

    assert "Willem" not in redacted.text
    assert "Boer" not in redacted.text


def test_short_name_parts_are_left_alone():
    """ "de" and "van" match half a CV; only parts longer than two letters go."""
    redacted = redact_cv("Jan de Vries werkte de hele dag", name="Jan de Vries")

    assert "de hele dag" in redacted.text


@pytest.mark.parametrize(
    "line",
    [
        "Orderpicker van 2016 tot 2019 en 2021 bij Action",  # 2019 en -> not a postcode
        "Ad-hoc analyses sinds 2023 Ad-hoc voor Logistiek",
        "Werkte in 2018 in Utrecht",
    ],
)
def test_prose_is_not_mistaken_for_a_postcode(line):
    assert redact_cv(line).text == line


@pytest.mark.parametrize(
    "phone",
    ["Tel: 06 - 3318 2245", "06 12345678", "+47 412 88 907", "+31 (0)70 700 0510"],
)
def test_phone_numbers_in_the_shapes_people_write_them(phone):
    assert "[phone removed]" in redact_cv(phone).text


def test_a_birth_line_disappears_entirely():
    redacted = redact_cv("Naam: X\nGeboren op 2 februari 1994\nOpleiding: mbo")

    assert "1994" not in redacted.text
    assert "februari" not in redacted.text
    assert "Opleiding: mbo" in redacted.text
