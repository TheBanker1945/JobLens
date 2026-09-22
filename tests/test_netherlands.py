"""The Dutch filter and the cap, and above all the order they run in."""

from joblens.sources.base import Vacancy
from joblens.sources.netherlands import is_dutch, select

MARKERS = ["Netherlands", "Amsterdam", "Utrecht"]


def job(number: int, place: str, country: str | None = None) -> Vacancy:
    """A board job the way Greenhouse sends it: the place only in `location`."""
    return Vacancy(
        source="greenhouse",
        source_id=str(number),
        url=f"https://example.test/{number}",
        title=f"Job {number}",
        country=country,
        text="A real vacancy.",
        raw={"location": {"name": place}},
    )


def test_a_worldwide_board_is_filtered_before_it_is_capped():
    """The bug: Adyen lists its Dutch jobs after position 100 of a worldwide
    board, and a cap applied first never saw them."""
    board = [job(n, "Paris") for n in range(100)] + [
        job(n, "Amsterdam") for n in range(100, 150)
    ]

    selection = select(board, MARKERS, limit=100)

    assert len(selection.kept) == 50
    assert all(
        vacancy.raw["location"]["name"] == "Amsterdam" for vacancy in selection.kept
    )
    assert selection.dutch == 50
    assert selection.capped == 0


def test_the_cap_only_counts_dutch_vacancies_and_says_what_it_left_out():
    board = [job(n, "Utrecht") for n in range(30)] + [
        job(n, "Berlin") for n in range(30, 40)
    ]

    selection = select(board, MARKERS, limit=20)

    assert len(selection.kept) == 20
    assert selection.dutch == 30
    assert selection.capped == 10


def test_no_limit_keeps_every_dutch_vacancy():
    board = [job(n, "Amsterdam") for n in range(250)]

    assert len(select(board, MARKERS, limit=None).kept) == 250


def test_all_countries_skips_the_filter_but_not_the_cap():
    board = [job(1, "Paris"), job(2, "Berlin"), job(3, "Amsterdam")]

    selection = select(board, MARKERS, limit=2, all_countries=True)

    assert [vacancy.source_id for vacancy in selection.kept] == ["1", "2"]
    assert selection.capped == 1


def test_a_country_code_is_enough_without_a_known_place():
    assert is_dutch(job(1, "Zeist", country="NL"), MARKERS)
    assert is_dutch(job(2, "Amsterdam, North Holland"), MARKERS)
    assert not is_dutch(job(3, "Remote - EMEA"), MARKERS)
