"""The viewer: the three buckets, and a server that refuses what it should.

The API functions are tested directly because they are plain functions over a
store and a corpus. The server gets one test of its own, over a real socket,
because routing and path handling are exactly the parts that a unit test of the
handler would mock away.
"""

import json
import threading
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest
from conftest import details as make_details

from joblens.corpus import Corpus, Funnel
from joblens.cv.documents import QueryPart
from joblens.cv.judge import Judged, MatchJudgement
from joblens.cv.match import CVMatch
from joblens.cv.outcome import assess
from joblens.cv.runs import RunStamp, build_record
from joblens.sources.base import Vacancy
from joblens.storage import FileStore
from joblens.web import api
from joblens.web.server import Viewer, serve

ROOT_WEB = Path(__file__).resolve().parents[1] / "web"


def vacancy(number: int, title: str = "Data Engineer") -> Vacancy:
    return Vacancy(
        source="indeed",
        source_id=str(number),
        url=f"https://example.test/{number}",
        title=title,
        company="Acme",
        city="Utrecht",
        text=f"Wij vragen ervaring met Python. Vacature {number}.",
    )


def judged(number: int, verdict: str, fit: int) -> Judged:
    judgement = MatchJudgement.model_validate(
        {
            "verdict": verdict,
            "fit": fit,
            "summary": "Een samenvatting.",
            "evidence": [],
            "gaps": [
                {
                    "requirement": "vier jaar ervaring",
                    "vacancy_quote": "Je hebt minimaal 4 jaar ervaring.",
                    "required": True,
                }
            ],
        }
    )
    match = CVMatch(vacancy(number), 0.70, "document", QueryPart("the whole CV", "cv"))
    return Judged(match=match, judgement=judgement, dropped=[], quotes=1)


def corpus_of(count: int = 6) -> Corpus:
    vacancies = [vacancy(n) for n in range(1, count + 1)]
    details = {v.key: make_details(v.title, city="Utrecht") for v in vacancies}
    return Corpus("raw", vacancies, details, Funnel(loaded=count + 2, duplicates=2))


def stored_run(store: FileStore, corpus: Corpus, *, judge_count: int = 3) -> str:
    runs = [
        judged(1, "strong", 80),
        judged(2, "possible", 55),
        judged(3, "weak", 20),
    ][:judge_count]
    stamp = RunStamp.model_validate(
        {
            "cv_name": "mahdi",
            "cv_digest": "9f1c2b84",
            "corpus": "raw",
            "corpus_size": len(corpus),
            "corpus_digest": "aaaaaaaa",
            "embed_model": "gemini-embedding-2",
            "judge_model": "gemini-3.8-flash",
            "cv_style": "raw",
            "prompt_version": "3.6",
            "top": judge_count,
        }
    )
    ranking = [
        CVMatch(v, 0.71 - n / 100, "document", QueryPart("the whole CV", "cv"))
        for n, v in enumerate(corpus.vacancies, 1)
    ]
    record = build_record(
        stamp,
        runs,
        assess(runs, corpus=len(corpus), corpus_name="raw"),
        ranking=ranking,
        shortlisted=judge_count,
        funnel=corpus.funnel,
        cost_usd=0.03,
    )
    return store.save_run(record)


def test_a_run_splits_into_read_and_recommended_and_never_seen(tmp_path):
    store, corpus = FileStore(tmp_path), corpus_of()
    run_id = stored_run(store, corpus)

    view = api.run_view(store, corpus, run_id)

    assert [row["key"] for row in view["recommended"]] == ["indeed:1", "indeed:2"]
    assert [row["key"] for row in view["rejected"]] == ["indeed:3"]
    # The rejection the brief is about: ranked, scored, and never read.
    assert [row["key"] for row in view["never_shortlisted"]] == [
        "indeed:4",
        "indeed:5",
        "indeed:6",
    ]
    assert view["counts"]["ranked"] == 6
    assert all(row["judged"] is False for row in view["never_shortlisted"])
    assert view["never_shortlisted"][0]["rank"] == 4


def test_the_page_is_told_where_the_cut_was_and_how_close_it_was(tmp_path):
    store, corpus = FileStore(tmp_path), corpus_of()

    view = api.run_view(store, corpus, stored_run(store, corpus))

    assert view["boundary"]["last_read"]["rank"] == 3
    assert view["boundary"]["first_unread"]["rank"] == 4
    assert view["boundary"]["gap"] == pytest.approx(0.01)
    assert "duplicates" in view["funnel_line"]


def test_the_headline_comes_from_the_code_that_is_allowed_to_write_it(tmp_path):
    """One sentence about a refusal, not a second opinion rebuilt in JavaScript."""
    store, corpus = FileStore(tmp_path), corpus_of()
    run_id = stored_run(store, corpus, judge_count=1)

    view = api.run_view(store, corpus, run_id)

    assert view["outcome"]["fit"] == "ok"
    assert "1 of 1 judged vacancies is a strong match." in view["outcome"]["headline"]


def test_a_vacancy_carries_its_text_its_fields_and_what_the_run_said(tmp_path):
    store, corpus = FileStore(tmp_path), corpus_of()
    run_id = stored_run(store, corpus)

    found = api.vacancy_view(store, corpus, "indeed:3", run_id)

    assert found["judgement"]["verdict"] == "weak"
    assert found["judgement"]["gaps"][0]["requirement"] == "vier jaar ervaring"
    assert "Wij vragen ervaring" in found["text"]
    assert found["details"]["title"] == "Data Engineer"

    unread = api.vacancy_view(store, corpus, "indeed:5", run_id)

    assert unread["judgement"] is None  # nobody read it
    assert unread["ranked"]["rank"] == 5


def test_the_corpus_can_be_searched_without_a_run(tmp_path):
    corpus = corpus_of()

    everything = api.corpus_view(corpus)
    one = api.corpus_view(corpus, "vacature 4")

    assert everything["total"] == 6 and everything["matched"] == 6
    assert [row["key"] for row in one["vacancies"]] == ["indeed:4"]
    assert one["vacancies"][0]["extracted"] is True


def test_a_missing_run_or_vacancy_raises_rather_than_inventing_one(tmp_path):
    store, corpus = FileStore(tmp_path), corpus_of()
    with pytest.raises(KeyError):
        api.run_view(store, corpus, "2026-01-01_0000_nobody")
    with pytest.raises(KeyError):
        api.vacancy_view(store, corpus, "indeed:999")


@pytest.fixture
def live(tmp_path):
    """The real server on a real port, so routing is what is being tested."""
    store, corpus = FileStore(tmp_path), corpus_of()
    run_id = stored_run(store, corpus)
    server = serve(Viewer(store, corpus, ROOT_WEB, judged_by="Mahdi"), port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address
    yield f"http://127.0.0.1:{port}", run_id
    server.shutdown()
    server.server_close()


def get(url: str) -> dict:
    with urlopen(url) as answer:  # noqa: S310 - our own localhost server
        return json.loads(answer.read())


def post(url: str, body: dict) -> dict:
    request = Request(  # noqa: S310
        url,
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urlopen(request) as answer:  # noqa: S310
        return json.loads(answer.read())


def test_the_server_answers_json_and_serves_the_page(live):
    base, run_id = live

    assert len(get(f"{base}/api/runs")["runs"]) == 1
    assert get(f"{base}/api/runs/{run_id}")["counts"]["never_shortlisted"] == 3
    assert get(f"{base}/api/vacancy?key=indeed:1&run={run_id}")["details"] is not None

    with urlopen(f"{base}/") as answer:  # noqa: S310
        assert b"JobLens" in answer.read()
        assert answer.headers["Content-Type"].startswith("text/html")


def test_the_server_refuses_what_it_should(live):
    base, _ = live

    for path, status in [
        ("/api/runs/2026-01-01_0000_nobody", 404),
        ("/api/runs/..%2F..%2Fsecret", 404),
        ("/api/vacancy", 400),  # no key
        ("/api/corpus?limit=many", 400),
        ("/api/nothing", 404),
        ("/../.env", 404),
        ("/app.js/../../pyproject.toml", 404),
    ]:
        with pytest.raises(HTTPError) as raised:
            urlopen(f"{base}{path}")  # noqa: S310
        assert raised.value.code == status, path


def test_marking_a_vacancy_needs_a_reason_and_is_stored_with_the_run(tmp_path):
    store, corpus = FileStore(tmp_path), corpus_of()
    run_id = stored_run(store, corpus)

    with pytest.raises(ValueError, match="reason is required"):
        api.record_decision(
            store,
            corpus,
            run_id,
            {"key": "indeed:3", "call": "apply", "reason": " ", "judged_by": "Mahdi"},
        )

    view = api.record_decision(
        store,
        corpus,
        run_id,
        {
            "key": "indeed:3",
            "call": "apply",
            "reason": "they ask 4 years, I would apply anyway",
            "judged_by": "Mahdi",
        },
    )

    assert view["counts"] == {"apply": 1, "maybe": 0, "judged": 1, "with_a_reason": 1}
    stored = store.load_labels("mahdi")
    decision = stored.decision_for("indeed:3")
    # What the judge had said at that moment, kept next to what the person said.
    assert (decision.verdict, decision.fit, decision.rank) == ("weak", 20, 3)
    assert decision.run == run_id
    assert stored.relevant == ["indeed:3"]  # the eval reads this, unchanged


def test_a_vacancy_nobody_read_can_be_marked_too(tmp_path):
    """The rejection the whole phase is about: #4, never shortlisted."""
    store, corpus = FileStore(tmp_path), corpus_of()
    run_id = stored_run(store, corpus)

    api.record_decision(
        store,
        corpus,
        run_id,
        {
            "key": "indeed:5",
            "call": "apply",
            "reason": "this is exactly my work and it was never read",
            "judged_by": "Mahdi",
        },
    )

    decision = store.load_labels("mahdi").decision_for("indeed:5")
    assert decision.rank == 5
    assert decision.verdict == ""  # no model ever had an opinion about it
    assert decision.fit is None


def test_labels_cannot_be_written_without_a_name_on_them(tmp_path):
    store, corpus = FileStore(tmp_path), corpus_of()
    run_id = stored_run(store, corpus)

    with pytest.raises(ValueError, match="judged-by"):
        api.record_decision(
            store, corpus, run_id, {"key": "indeed:1", "call": "no", "reason": "nope"}
        )


def test_marking_refuses_a_vacancy_that_is_not_in_the_corpus(tmp_path):
    store, corpus = FileStore(tmp_path), corpus_of()
    run_id = stored_run(store, corpus)

    for body in [
        {"key": "indeed:999", "call": "apply", "reason": "x", "judged_by": "M"},
        {"key": "indeed:1", "call": "shrug", "reason": "x", "judged_by": "M"},
        {"key": "", "call": "apply", "reason": "x", "judged_by": "M"},
    ]:
        with pytest.raises(ValueError):
            api.record_decision(store, corpus, run_id, body)


def test_the_server_writes_a_decision_and_reads_it_back(live):
    base, run_id = live
    url = f"{base}/api/runs/{run_id}/labels"

    assert get(url)["decisions"] == {}

    answer = post(url, {"key": "indeed:2", "call": "maybe", "reason": "salary unclear"})

    assert answer["counts"]["with_a_reason"] == 1
    assert get(url)["decisions"]["indeed:2"]["reason"] == "salary unclear"

    with pytest.raises(HTTPError) as raised:
        post(url, {"key": "indeed:2", "call": "maybe", "reason": ""})
    assert raised.value.code == 400


def test_a_post_that_is_not_json_is_refused_and_writes_nothing(live):
    """A form on another site can send text/plain without asking first."""
    base, run_id = live
    url = f"{base}/api/runs/{run_id}/labels"
    request = Request(  # noqa: S310
        url,
        data=json.dumps({"key": "indeed:2", "call": "no", "reason": "x"}).encode(),
        headers={"Content-Type": "text/plain"},
        method="POST",
    )
    with pytest.raises(HTTPError) as raised:
        urlopen(request)  # noqa: S310
    assert raised.value.code == 400
    assert get(url)["decisions"] == {}


def test_a_request_for_another_host_is_refused(live):
    """DNS rebinding: a domain that resolves to 127.0.0.1 is not this server."""
    base, _ = live
    request = Request(f"{base}/api/runs", headers={"Host": "evil.example"})  # noqa: S310
    with pytest.raises(HTTPError) as raised:
        urlopen(request)  # noqa: S310
    assert raised.value.code == 403


def test_marks_made_at_the_same_moment_are_all_kept(live, tmp_path):
    """Each request has its own thread; without a lock, one save erased another."""
    base, run_id = live
    url = f"{base}/api/runs/{run_id}/labels"
    errors: list[Exception] = []

    def mark(n: int) -> None:
        try:
            post(url, {"key": f"indeed:{1 + n % 6}", "call": "no", "reason": f"r{n}"})
        except Exception as err:  # noqa: BLE001 - reported below, not swallowed
            errors.append(err)

    threads = [threading.Thread(target=mark, args=(n,)) for n in range(24)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert errors == []  # a request that failed would otherwise look like a lost mark
    store = FileStore(tmp_path)
    cv = store.load_run(run_id).stamp.cv_name
    assert len(store.load_labels(cv).decisions) == 24
    assert not list(tmp_path.rglob("*.tmp"))  # no half-written file left behind
