from conftest import details

from joblens.extraction.store import DetailsStore, ExtractedVacancy


def record(key: str, title: str, model: str = "gemini-3.8-flash") -> ExtractedVacancy:
    return ExtractedVacancy(key=key, model=model, details=details(title))


def test_extractions_are_stored_and_read_back(tmp_path):
    store = DetailsStore(tmp_path)

    store.add("indeed", [record("indeed:1", "Data Engineer")])

    stored = store.load("indeed")
    assert stored["indeed:1"].details.title == "Data Engineer"
    assert stored["indeed:1"].extracted_at is not None


def test_a_second_extraction_replaces_the_first(tmp_path):
    store = DetailsStore(tmp_path)

    store.add("indeed", [record("indeed:1", "Data Enigneer")])
    store.add("indeed", [record("indeed:1", "Data Engineer", model="qwen3:8b")])

    newest = store.load("indeed")["indeed:1"]
    assert len(newest.details.title) and newest.details.title == "Data Engineer"
    assert newest.model == "qwen3:8b"
    assert len(store.path_for("indeed").read_text().splitlines()) == 2  # both kept


def test_details_from_every_source_come_back_together(tmp_path):
    store = DetailsStore(tmp_path)
    store.add("indeed", [record("indeed:1", "Data Engineer")])
    store.add("recruitee", [record("recruitee:7", "Verpleegkundige")])

    everything = store.load_all(["indeed", "recruitee"])

    assert sorted(everything) == ["indeed:1", "recruitee:7"]


def test_nothing_to_store_is_fine(tmp_path):
    assert DetailsStore(tmp_path).add("indeed", []) == 0
    assert DetailsStore(tmp_path).load("indeed") == {}
