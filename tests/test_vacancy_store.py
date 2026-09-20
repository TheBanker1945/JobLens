from joblens.sources.base import Vacancy
from joblens.sources.store import StoreResult, VacancyStore


def vacancy(source_id: str, title: str = "Developer", **fields) -> Vacancy:
    return Vacancy(
        source="recruitee",
        source_id=source_id,
        url="https://example.test",
        title=title,
        text="Tekst",
        **fields,
    )


def test_vacancies_are_stored_and_read_back(tmp_path):
    store = VacancyStore(tmp_path)

    result = store.add([vacancy("1"), vacancy("2")])

    assert result == StoreResult(stored=2)
    assert [v.source_id for v in store.load("recruitee")] == ["1", "2"]


def test_known_vacancies_are_skipped(tmp_path):
    store = VacancyStore(tmp_path)
    store.add([vacancy("1")])

    result = store.add([vacancy("1"), vacancy("2")])

    assert result == StoreResult(stored=1, known=1)
    assert len(store.load("recruitee")) == 2


def test_duplicates_inside_one_batch_are_skipped(tmp_path):
    store = VacancyStore(tmp_path)

    assert store.add([vacancy("1"), vacancy("1")]) == StoreResult(stored=1, known=1)


def test_the_same_job_from_another_board_is_not_stored_twice(tmp_path):
    store = VacancyStore(tmp_path)
    indeed = vacancy("1", "(Senior) Data Engineer", company="Adyen", city="Amsterdam")
    linkedin = indeed.model_copy(
        update={"source": "linkedin", "source_id": "9", "title": "Senior data engineer"}
    )

    store.add([indeed])
    result = store.add([linkedin])

    assert result == StoreResult(duplicate=1)  # punctuation and case do not matter
    assert store.load("linkedin") == []


def test_a_different_job_at_the_same_company_is_kept(tmp_path):
    store = VacancyStore(tmp_path)
    first = vacancy("1", "Data Engineer", company="Adyen", city="Amsterdam")
    other_city = first.model_copy(update={"source_id": "2", "city": "Rotterdam"})
    other_role = first.model_copy(update={"source_id": "3", "title": "Data Analyst"})

    assert store.add([first, other_city, other_role]) == StoreResult(stored=3)


def test_without_a_company_nothing_is_called_a_duplicate(tmp_path):
    """Two "Developer" vacancies from different boards may well be two jobs."""
    store = VacancyStore(tmp_path)
    nameless = vacancy("1")
    elsewhere = nameless.model_copy(update={"source": "greenhouse", "source_id": "2"})

    store.add([nameless])

    assert store.add([elsewhere]) == StoreResult(stored=1)


def test_sources_are_stored_separately(tmp_path):
    store = VacancyStore(tmp_path)
    other = vacancy("1").model_copy(update={"source": "greenhouse"})

    store.add([vacancy("1")])
    store.add([other])

    assert (tmp_path / "recruitee.jsonl").exists()
    assert (tmp_path / "greenhouse.jsonl").exists()
    assert store.sources() == ["greenhouse", "recruitee"]


def test_nothing_to_store_is_fine(tmp_path):
    assert VacancyStore(tmp_path).add([]) == StoreResult()
    assert VacancyStore(tmp_path).load("recruitee") == []


def test_when_we_saw_a_vacancy_is_recorded(tmp_path):
    store = VacancyStore(tmp_path)

    store.add([vacancy("1")])

    assert store.load("recruitee")[0].fetched_at is not None
