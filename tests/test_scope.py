"""The scope: which new vacancies are kept, by place and by the kind of work.

Every case here is one the stored corpus or Mahdi's labels produced on
2026-09-22, or a trap found while measuring on them.
"""

import tomllib
from pathlib import Path

import pytest

from joblens.sources.base import Vacancy
from joblens.sources.netherlands import select
from joblens.sources.scope import Places, Scope

ROOT = Path(__file__).parent.parent
CONFIG = tomllib.loads((ROOT / "sources.toml").read_text(encoding="utf-8"))
PLACES = Places.load()
SCOPE = Scope.from_config(CONFIG["scope"], PLACES)


def job(title: str, location: str, city: str | None = None, n: int = 1) -> Vacancy:
    return Vacancy(
        source="indeed",
        source_id=str(n),
        url=f"https://example.test/{n}",
        title=title,
        city=city,
        text="A real vacancy.",
        raw={"location": location},
    )


# --- where ------------------------------------------------------------------------


def test_small_places_count_as_much_as_big_cities():
    """A hand-written city list would know Rotterdam and miss these."""
    for place in ("Goes", "Breukelen", "Schiphol", "Koudekerk aan den Rijn"):
        assert SCOPE.where(f"{place}, Nederland") == "", place


def test_places_outside_the_provinces_are_named():
    reason = SCOPE.where("Eindhoven, NB, NL")

    assert reason.startswith("outside the provinces")
    assert "eindhoven (Noord-Brabant)" in reason


def test_how_the_boards_write_it():
    """JobSpy: "Breukelen, UT, NL"; Greenhouse: English province names."""
    assert PLACES.find("Breukelen, UT, NL") == {
        "breukelen": {"Utrecht"},
        "UT": {"Utrecht"},
    }
    assert "north holland" in PLACES.find("Amsterdam, North Holland, Netherlands")
    assert PLACES.find("The Hague")["the hague"] == {"Zuid-Holland"}
    assert PLACES.find("Den Haag")["den haag"] == {"Zuid-Holland"}


def test_the_longest_name_wins():
    """ "Alphen" alone is in Brabant and Gelderland; this one is in Zuid-Holland."""
    assert PLACES.find("Alphen aan den Rijn") == {
        "alphen aan den rijn": {"Zuid-Holland"}
    }


def test_nederland_is_the_country_not_the_hamlet_in_overijssel():
    """Found by looking up every stored location: "Nederland" is a place in
    Overijssel, and a bare "Nederland" would have dropped a Dutch vacancy."""
    assert PLACES.find("Nederland") == {}
    assert SCOPE.where("Nederland") == ""


def test_a_bare_name_means_the_place_cbs_left_without_a_suffix():
    """CBS writes "Rijswijk (NB)" and "Rijswijk (GLD)"; a board writes "Rijswijk"."""
    assert PLACES.find("Rijswijk") == {"rijswijk": {"Zuid-Holland"}}


def test_a_name_that_only_exists_with_suffixes_means_all_of_them():
    assert PLACES.find("Bergen")["bergen"] == {"Noord-Holland", "Limburg"}
    assert SCOPE.where("Bergen") == ""  # one of them is in the scope: kept


def test_a_location_that_names_no_dutch_place_is_kept():
    """A board that does not say where cannot be used to argue it is elsewhere."""
    for location in ("Remote - Netherlands", "Netherlands", "", "Belgium; Netherlands"):
        assert SCOPE.where(location) == "", location


def test_one_chosen_place_among_many_is_enough():
    assert SCOPE.where("Amsterdam; London") == ""
    assert SCOPE.where("Eindhoven, Utrecht") == ""


def test_a_province_that_does_not_exist_is_refused():
    with pytest.raises(ValueError, match="Noord Holland"):
        Scope(["Noord Holland"], ["developer"], places=PLACES)


# --- what kind of work -----------------------------------------------------------


@pytest.mark.parametrize(
    "title",
    [
        # Mahdi's "would apply" and "maybe" labels, 2026-09-22: all ten must stay.
        "Full-stack Developer",
        "Python Software Engineer -  AI team",
        "Student AI Developer (studying in NL)",
        "Front-end Developer",
        "Forward Deployed Engineer Lead",
        # Dutch compounds, and the two the first word list missed.
        "Softwareontwikkelaar",
        "Webdeveloper",
        "QA Lead (hybrid, full time)",
        "Team Lead - Haskell Platform Team",
        "Salesforce Developer",
        # Missed by the first real run through the scope, 2026-09-23.
        "CI/CD Specialist - EMS Platform Team",
        "Team Lead - Platform Team",
    ],
)
def test_software_and_ai_work_is_in_scope(title):
    assert SCOPE.what(title).keep, title


@pytest.mark.parametrize(
    "title",
    [
        "Verpleegkundige",
        "Servicemonteur",
        "Business Developer Maritime Services",
        "Beleidsontwikkelaar",
        "Werktuigbouwkundig Engineer",
        "Field Service Engineer",
        "Senior Product Manager - Developer Experience",
        "Solutions Architect (Pre-sales) - Benelux Strategic Accounts",
        "Tech Recruitment Business Partner",
        "Docent Software Development",
        # Kept by the first real run, and not the work, 2026-09-23.
        "Planontwikkelaar Renovatie en Verduurzaming",
        "Senior thermal-hydraulics engineer",
    ],
)
def test_other_work_is_not(title):
    assert not SCOPE.what(title).keep, title


def test_short_words_must_stand_alone():
    """ "ai" would otherwise match "detail" and "trainee"."""
    assert not SCOPE.what("Detailhandel medewerker").keep
    assert not SCOPE.what("Trainee Logistiek").keep
    assert SCOPE.what("Medewerker AI").keep


def test_risky_language_names_are_left_out_of_the_word_list():
    """Longer words match inside other words: "rust" sits in Rustoord, "scala"
    in Escalatie, "react" in Reactor. Those three are not role words."""
    assert not SCOPE.what("Verpleegkundige Rustoord").keep
    assert not SCOPE.what("Escalatiemanager").keep
    assert not SCOPE.what("Operator reactor").keep


def test_the_reason_says_which_word_decided():
    assert SCOPE.what("Servicemonteur").reason == "not the work: 'servicemonteur'"
    assert SCOPE.what("Data Engineer").reason == "in scope: 'engineer'"


# --- both, on a vacancy, and in the order the fetch uses -----------------------------


def test_a_vacancy_needs_both_the_place_and_the_work():
    assert SCOPE.keep(job("Python Developer", "Delft, Zuid-Holland, Nederland"))
    assert not SCOPE.keep(job("Python Developer", "Eindhoven, Noord-Brabant"))
    assert not SCOPE.keep(job("Verpleegkundige", "Utrecht, Utrecht, Nederland"))


def test_the_city_field_counts_as_well_as_the_location():
    assert not SCOPE.keep(job("Python Developer", "", city="Eindhoven"))


def test_the_scope_runs_before_the_cap():
    """The board-cap lesson again: a cap first spends its places on jobs the
    scope would throw away."""
    board = [job("Verpleegkundige", "Utrecht, Nederland", n=n) for n in range(10)] + [
        job("Python Developer", "Utrecht, Nederland", n=n) for n in range(10, 15)
    ]

    selection = select(board, ["Nederland"], limit=5, scope=SCOPE)

    assert [v.title for v in selection.kept] == ["Python Developer"] * 5
    assert selection.dutch == 15
    assert selection.out_of_scope == 10
    assert selection.capped == 0
    assert selection.left_out[0] == (
        "Verpleegkundige -- not the work: no role word in the title"
    )


def test_without_a_scope_every_dutch_vacancy_is_kept():
    board = [job("Verpleegkundige", "Utrecht, Nederland", n=n) for n in range(3)]

    selection = select(board, ["Nederland"], limit=None)

    assert len(selection.kept) == 3
    assert selection.out_of_scope == 0
