"""Loading the two corpora into one shape, from a repo root built in a tmpdir."""

import json

from conftest import details as make_details

from joblens.corpus import load_corpus
from joblens.extraction.store import DetailsStore, ExtractedVacancy
from joblens.sources.base import Vacancy
from joblens.sources.store import VacancyStore


def build_root(tmp_path, *, extract_sample=True, extract_raw=True):
    samples = tmp_path / "data" / "samples"
    (samples / "vacancies").mkdir(parents=True)
    (samples / "vacancies" / "01_data_analist.txt").write_text(
        "Data Analist\nJe werkt met SQL.", encoding="utf-8"
    )
    if extract_sample:
        (samples / "extracted").mkdir()
        (samples / "extracted" / "01_data_analist.json").write_text(
            json.dumps(
                {"details": make_details("Data Analist", city="Utrecht").model_dump()}
            ),
            encoding="utf-8",
        )

    raw = tmp_path / "data" / "raw"
    store = VacancyStore(raw / "vacancies")
    vacancy = Vacancy(
        source="greenhouse",
        source_id="99",
        url="https://example.test/99",
        title="Backend Developer",
        text="Je bouwt API's in Python.",
    )
    store.add([vacancy])
    if extract_raw:
        DetailsStore(raw / "extracted").add(
            "greenhouse",
            [
                ExtractedVacancy(
                    key=vacancy.key,
                    model="test",
                    details=make_details("Backend Developer"),
                )
            ],
        )
    return tmp_path


def test_a_sample_becomes_a_vacancy_keyed_by_its_filename(tmp_path):
    corpus = load_corpus("samples", root=build_root(tmp_path))

    (vacancy,) = corpus.vacancies
    assert vacancy.key == "sample:01_data_analist"
    assert vacancy.city == "Utrecht"  # from the extraction, not from the text
    assert vacancy.url == "data/samples/vacancies/01_data_analist.txt"


def test_a_sample_without_an_extraction_falls_back_to_its_first_line(tmp_path):
    root = build_root(tmp_path, extract_sample=False)

    corpus = load_corpus("samples", root=root)

    (vacancy,) = corpus.vacancies
    assert (vacancy.title, vacancy.city) == ("Data Analist", None)
    assert corpus.details == {}


def test_raw_vacancies_are_loaded_with_their_stored_details(tmp_path):
    corpus = load_corpus("raw", root=build_root(tmp_path))

    assert [v.key for v in corpus.vacancies] == ["greenhouse:99"]
    assert corpus.details["greenhouse:99"].title == "Backend Developer"
    assert corpus.by_key()["greenhouse:99"].title == "Backend Developer"


def test_extracted_drops_vacancies_that_have_no_details(tmp_path):
    corpus = load_corpus("raw", root=build_root(tmp_path, extract_raw=False))

    assert len(corpus) == 1
    assert len(corpus.extracted()) == 0


def test_an_open_application_page_is_not_a_vacancy(tmp_path):
    root = build_root(tmp_path)
    store = VacancyStore(root / "data" / "raw" / "vacancies")
    store.add(
        [
            Vacancy(
                source="recruitee",
                source_id="1",
                url="https://example.test/1",
                title="Open sollicitatie",
                text="Stuur ons een open sollicitatie.",
            ),
            Vacancy(
                source="recruitee",
                source_id="2",
                url="https://example.test/2",
                title="Open Application",
                text="We are always open to receiving applications.",
            ),
            Vacancy(
                source="recruitee",
                source_id="3",
                url="https://example.test/3",
                title="Open Source Engineer",  # "open" alone is not the signal
                text="Je werkt aan open source software.",
            ),
        ]
    )

    corpus = load_corpus("raw", root=root)

    assert [v.title for v in corpus.vacancies if v.source == "recruitee"] == [
        "Open Source Engineer"
    ]


def test_a_job_stored_twice_is_loaded_once(tmp_path):
    root = build_root(tmp_path)
    store = VacancyStore(root / "data" / "raw" / "vacancies")
    first = Vacancy(
        source="indeed",
        source_id="1",
        url="https://example.test/1",
        title="Algemeen of Gespecialiseerd Verpleegkundige",
        city="Utrecht",
        text="Ben jij verpleegkundige en toe aan iets anders?",
    )
    again = first.model_copy(update={"source_id": "2", "city": "UT"})
    # Appended straight to the file: this is a store written before `add` could
    # catch it, which is exactly the state data/raw/ was found in.
    path = store.path_for("indeed")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        first.model_dump_json() + "\n" + again.model_dump_json() + "\n",
        encoding="utf-8",
    )
    assert len(store.load("indeed")) == 2  # both really are on disk

    corpus = load_corpus("raw", root=root)

    assert [v.key for v in corpus.vacancies if v.source == "indeed"] == ["indeed:1"]


def test_the_funnel_counts_what_never_reached_the_ranking(tmp_path):
    """The rejections with no score: filtered, deduped, or never extracted.

    A stored ranking can explain why vacancy #83 was not read. It cannot say
    anything about a vacancy that is not in the ranking at all, so the three
    ways of leaving before that point are counted where they happen.
    """
    root = build_root(tmp_path)
    store = VacancyStore(root / "data" / "raw" / "vacancies")
    store.add(
        [
            Vacancy(
                source="recruitee",
                source_id="1",
                url="https://example.test/1",
                title="Open sollicitatie",
                text="Stuur ons een open sollicitatie.",
            )
        ]
    )
    analyst = Vacancy(
        source="indeed",
        source_id="1",
        url="https://example.test/1",
        title="Data Analist",
        city="Utrecht",
        text="Je werkt met SQL.",
    )
    path = store.path_for("indeed")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        analyst.model_dump_json()
        + "\n"
        + analyst.model_copy(update={"source_id": "2"}).model_dump_json()
        + "\n",
        encoding="utf-8",
    )

    corpus = load_corpus("raw", root=root)

    assert corpus.funnel.loaded == 4  # one greenhouse, one recruitee, two indeed
    assert corpus.funnel.not_a_vacancy == 1
    assert corpus.funnel.duplicates == 1
    assert corpus.funnel.not_extracted == 0  # not known until extracted() is asked

    extracted = corpus.extracted()

    assert len(extracted) == 1  # only the greenhouse one has details
    assert extracted.funnel.not_extracted == 1
    assert extracted.funnel.dropped == 3
    assert "3 never had a chance" in extracted.funnel.line()
