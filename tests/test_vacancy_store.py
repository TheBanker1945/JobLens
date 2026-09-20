from joblens.sources.base import Vacancy
from joblens.sources.store import VacancyStore


def vacancy(source_id: str, title: str = "Developer") -> Vacancy:
    return Vacancy(
        source="recruitee",
        source_id=source_id,
        url="https://example.test",
        title=title,
        text="Tekst",
    )


def test_vacancies_are_stored_and_read_back(tmp_path):
    store = VacancyStore(tmp_path)

    stored, skipped = store.add([vacancy("1"), vacancy("2")])

    assert (stored, skipped) == (2, 0)
    assert [v.source_id for v in store.load("recruitee")] == ["1", "2"]


def test_known_vacancies_are_skipped(tmp_path):
    store = VacancyStore(tmp_path)
    store.add([vacancy("1")])

    stored, skipped = store.add([vacancy("1"), vacancy("2")])

    assert (stored, skipped) == (1, 1)
    assert len(store.load("recruitee")) == 2


def test_duplicates_inside_one_batch_are_skipped(tmp_path):
    store = VacancyStore(tmp_path)

    assert store.add([vacancy("1"), vacancy("1")]) == (1, 1)


def test_sources_are_stored_separately(tmp_path):
    store = VacancyStore(tmp_path)
    other = vacancy("1").model_copy(update={"source": "greenhouse"})

    store.add([vacancy("1")])
    store.add([other])

    assert (tmp_path / "recruitee.jsonl").exists()
    assert (tmp_path / "greenhouse.jsonl").exists()
    assert len(store.load("greenhouse")) == 1


def test_nothing_to_store_is_fine(tmp_path):
    assert VacancyStore(tmp_path).add([]) == (0, 0)
    assert VacancyStore(tmp_path).load("recruitee") == []
