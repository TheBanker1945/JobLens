"""What leaves the machine when a CV is read, and what does not."""

import pytest
from conftest import DAMAGED_CV

from joblens.cv.clean import redact_cv
from joblens.cv.read import read_cv
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


def test_name_particles_are_left_alone_however_long():
    """ "van" and "der" are three letters, and used to go everywhere."""
    text = "Jan van der Berg\nOntwikkeling van dashboards voor der Kinderen"

    redacted = redact_cv(text, name="Jan van der Berg")

    assert "Ontwikkeling van dashboards voor der Kinderen" in redacted.text
    assert "Berg" not in redacted.text


@pytest.mark.parametrize("line", ["Jan 2021 - heden", "Jan. 2021", "jan '21"])
def test_a_first_name_that_is_also_a_month_keeps_the_date(line):
    assert line in redact_cv(f"Jan Jansen\n{line}", name="Jan Jansen").text


def test_a_first_name_still_goes_when_it_is_not_a_date():
    redacted = redact_cv("Jan Jansen\nReferentie: vraag naar Jan.", name="Jan Jansen")

    assert "Jan" not in redacted.text


@pytest.mark.parametrize(
    "line",
    [
        "Orderpicker van 2016 tot 2019 en 2021 bij Action",  # 2019 en -> not a postcode
        "Ad-hoc analyses sinds 2023 Ad-hoc voor Logistiek",
        "Werkte in 2018 in Utrecht",
        # A year and the acronym after it: both used to go as a postcode.
        "2019 - 2021 IT Consultant bij Capgemini",
        "Sinds 2023 AI engineer",
        "2020 QA tester, 2022 BI developer",
    ],
)
def test_prose_is_not_mistaken_for_a_postcode(line):
    assert redact_cv(line).text == line


@pytest.mark.parametrize(
    "line",
    [
        "Loopbaan\n2019 - 2023 Data-analist bij Coolblue",  # a heading, then a year
        "Bijbaan 2018 - 2020 kassamedewerker",  # baan is also a job
        "Juridisch medewerker, Gerechtshof\n2018 - 2021",
        "Vaste baan 2019, daarna zzp",
    ],
)
def test_a_job_heading_with_a_year_is_not_an_address(line):
    assert redact_cv(line).text == line


@pytest.mark.parametrize(
    "address", ["Maliebaan 12", "Hoofdweg 1080a", "Burgemeester de Withstraat 7"]
)
def test_street_lines_still_go(address):
    assert address not in redact_cv(f"{address}, 3581 CD Utrecht").text


def test_a_postcode_after_a_street_still_goes_even_in_the_haarlem_range():
    assert "2011 AB" not in redact_cv("Kruisstraat 5, 2011 AB Haarlem").text


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


def test_a_phone_number_whose_plus_was_eaten_by_the_font(tmp_path):
    """The privacy failure of 3.6.1, end to end. In a real PDF the "+" of
    "+31 6 ..." was a private-use glyph, so `PHONE` -- which needs a "+" or a
    leading "0" -- matched nothing and the number went to a cloud model."""
    path = tmp_path / "damaged.txt"
    path.write_text(DAMAGED_CV, encoding="utf-8")

    redacted = redact_cv(read_cv(path).text)

    assert "18295250" not in redacted.text
    assert "31 6 18295250" not in redacted.text
    assert redacted.counts()["phone"] == 1


@pytest.mark.parametrize(
    "phone",
    [
        "Tel 31 6 18295250",  # the "+" never survived the PDF
        "Tel 0031 70 700 0510",  # written for dialling from abroad
        "Bel 0612345678",  # one run of digits, no separators
        "Mobiel 49 176 12345678",  # a German number without its "+"
    ],
)
def test_phone_numbers_that_lost_their_plus(phone):
    assert not any(character.isdigit() for character in redact_cv(phone).text)


@pytest.mark.parametrize(
    "line",
    [
        "Salaris 32.000 - 38.000 per jaar",  # starts with a calling code
        "Salaris 3200 - 3800 per maand",
        "Beschikbaar 32-40 uur per week",
        "2021 2022 2023 2024 2025 2026",  # a row of years, not a number
        "Team van 44 mensen, 5000 orders per dag",
        "Python 3.11, Angular 15",
    ],
)
def test_numbers_that_are_not_a_phone_number_are_kept(line):
    assert redact_cv(line).text == line


def test_a_long_run_of_digits_goes_whatever_it_is():
    """Nine digits is a burgerservicenummer, eleven is a phone number typed
    solid, and a CV has no honest use for either."""
    redacted = redact_cv("BSN 123456789 en klantnummer 88001234567")

    assert "123456789" not in redacted.text
    assert "88001234567" not in redacted.text
