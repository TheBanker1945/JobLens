"""The match service: match_cv.py's pipeline as calls a web request can make.

Everything here runs on a fake model and a fake embedder, so it needs no key
and no network. That the service ranks exactly as the script did before 7.1
was checked once on the real corpus (docs/learning-log.md, 7.1); what these
tests keep true is the contract: settings in, an upload accepted, progress
reported, the run stored, and nothing but a ServiceError coming out.
"""

import json

import httpx
import openai
import pytest
from conftest import SAMPLE_CVS, FakeClient, scanned_pdf
from conftest import details as make_details
from test_cv_match import PROFILE

from joblens.config import LLMSettings
from joblens.corpus import Corpus
from joblens.cv.judge import MatchJudgement
from joblens.cv.read import CVFile
from joblens.service import (
    CVUnreadable,
    MatchRequest,
    Models,
    ProviderRefused,
    ProviderUnreachable,
    judge,
    rank,
)
from joblens.sources.base import Vacancy
from joblens.storage import FileStore

MODELS = Models(
    cv=LLMSettings(
        provider="fake", base_url="http://fake.test", api_key="k", model="fake-cv"
    ),
    embed=LLMSettings(
        provider="fake", base_url="http://fake.test", api_key="k", model="fake-embed"
    ),
)
SANNE = SAMPLE_CVS / "sanne_vermeulen.md"
WISHLIST = "Gezocht: verpleegkundige in de ggz"
JUDGEMENT = json.dumps(
    MatchJudgement(
        verdict="strong", fit=80, summary="Past.", evidence=[], gaps=[]
    ).model_dump(mode="json")
)


class CareOrData:
    """An embedding client in two dimensions: care, and data."""

    def __init__(self, settings):
        self.settings = settings  # CachedEmbedder keys its cache by the model
        self.closed = False

    def embed_documents(self, texts):
        return [self.vector(text) for text in texts]

    def query_text(self, query, task=""):
        return query

    def vector(self, text):
        low = text.lower()
        return [low.count("verpleeg") + 0.01, low.count("data") + 0.01]

    def close(self):
        self.closed = True


def vacancy(source_id: str, title: str, company: str) -> Vacancy:
    return Vacancy(
        source="indeed",
        source_id=source_id,
        url="https://example.test",
        title=title,
        company=company,
        text=title,
    )


CARE = vacancy("1", "Verpleegkundige verpleeghuis", "Altrecht")
CARE_TOO = vacancy("2", "Verpleegkundige", "Altrecht")  # same employer as CARE
DATA = vacancy("3", "Data analist", "Coolblue")
CORPUS = Corpus(
    "samples",
    [DATA, CARE_TOO, CARE],
    {v.key: make_details(v.title, company=v.company) for v in (CARE, CARE_TOO, DATA)},
)


def factories(client):
    """The two factories a call takes, handing out fakes instead of clients."""
    embedders = []

    def embed(settings):
        embedders.append(CareOrData(settings))
        return embedders[-1]

    return {"chat": lambda settings: client, "embed": embed}, embedders


def test_a_cv_is_ranked_judged_and_stored_with_progress_along_the_way(tmp_path):
    client = FakeClient(json.dumps(PROFILE), WISHLIST, JUDGEMENT, JUDGEMENT)
    made, embedders = factories(client)
    seen = []
    request = MatchRequest(cv=SANNE, top=2, per_employer=1)

    ranked = rank(
        request, CORPUS, MODELS, cache_dir=tmp_path, progress=seen.append, **made
    )
    run = judge(
        ranked,
        CORPUS,
        MODELS,
        cache_dir=tmp_path,
        store=FileStore(tmp_path),
        progress=seen.append,
        chat=made["chat"],
    )

    assert [m.vacancy for m in ranked.ranking] == [CARE, CARE_TOO, DATA]
    # The cap moves who is judged, never the ranking: CARE_TOO keeps its place.
    assert [m.vacancy for m in ranked.chosen.matches] == [CARE, DATA]
    assert [m.vacancy for m in ranked.chosen.capped] == [CARE_TOO]
    assert [(p.stage, p.done, p.total) for p in seen] == [
        ("reading", 0, 0),
        ("ranking", 0, 0),
        ("judging", 0, 2),
        ("judging", 1, 2),
        ("judging", 2, 2),
    ]
    assert len(run.judged) == 2 and run.failures == []
    assert run.cost_usd is None  # a model with no price is unknown, not free
    assert all(one.closed for one in embedders)

    stored = FileStore(tmp_path).load_run(run.run_id)
    assert stored.stamp.cv_name == "sanne_vermeulen"
    assert (stored.stamp.judge_model, stored.stamp.embed_model) == (
        "fake-cv",
        "fake-embed",
    )
    assert len(stored.ranking) == 3  # the whole ranking, not only the judged


def test_an_upload_is_ranked_the_same_as_the_file_it_came_from(tmp_path):
    from_disk = rank(
        MatchRequest(cv=SANNE, style="raw"),
        CORPUS,
        MODELS,
        cache_dir=tmp_path / "a",
        **factories(FakeClient(json.dumps(PROFILE)))[0],
    )
    uploaded = rank(
        MatchRequest(cv=CVFile(SANNE.name, SANNE.read_bytes()), style="raw"),
        CORPUS,
        MODELS,
        cache_dir=tmp_path / "b",
        **factories(FakeClient(json.dumps(PROFILE)))[0],
    )

    assert uploaded.prepared.name == from_disk.prepared.name == "sanne_vermeulen"
    assert uploaded.prepared.text == from_disk.prepared.text
    assert [m.vacancy.key for m in uploaded.ranking] == [
        m.vacancy.key for m in from_disk.ranking
    ]


def test_without_a_store_a_run_is_judged_and_not_kept(tmp_path):
    client = FakeClient(json.dumps(PROFILE), JUDGEMENT)
    made, _ = factories(client)
    ranked = rank(
        MatchRequest(cv=SANNE, style="raw", top=1),
        CORPUS,
        MODELS,
        cache_dir=tmp_path,
        **made,
    )

    run = judge(ranked, CORPUS, MODELS, cache_dir=tmp_path, chat=made["chat"])

    assert run.run_id is None
    assert len(run.record.rows) == 1


def test_one_vacancy_that_cannot_be_judged_is_a_line_not_an_error(tmp_path):
    client = FakeClient(json.dumps(PROFILE), "not json at all")
    made, _ = factories(client)
    ranked = rank(
        MatchRequest(cv=SANNE, style="raw", top=1),
        CORPUS,
        MODELS,
        cache_dir=tmp_path,
        **made,
    )

    run = judge(ranked, CORPUS, MODELS, cache_dir=tmp_path, chat=made["chat"])

    assert run.judged == []
    assert len(run.failures) == 1 and CARE.key in run.failures[0]


def test_a_scan_is_the_cvs_fault_and_says_so(tmp_path):
    made, _ = factories(FakeClient())

    with pytest.raises(CVUnreadable, match="picture of a CV"):
        rank(
            MatchRequest(cv=CVFile("scan.pdf", scanned_pdf())),
            CORPUS,
            MODELS,
            cache_dir=tmp_path,
            **made,
        )


class Failing:
    """A chat client whose provider answers every call with one error."""

    def __init__(self, error: Exception):
        self.error = error

    def chat(self, *args, **kwargs):
        raise self.error

    def close(self):
        pass


REQUEST = httpx.Request("POST", "http://fake.test/v1/chat/completions")


def test_no_answer_from_the_provider_is_unreachable(tmp_path):
    made, _ = factories(Failing(openai.APIConnectionError(request=REQUEST)))

    with pytest.raises(ProviderUnreachable, match="Cannot reach"):
        rank(MatchRequest(cv=SANNE), CORPUS, MODELS, cache_dir=tmp_path, **made)


@pytest.mark.parametrize(("status", "busy"), [(503, True), (401, False)])
def test_a_refusal_says_whether_waiting_will_help(tmp_path, status, busy):
    refusal = openai.APIStatusError(
        "no", response=httpx.Response(status, request=REQUEST), body=None
    )
    made, _ = factories(Failing(refusal))

    with pytest.raises(ProviderRefused) as caught:
        rank(MatchRequest(cv=SANNE), CORPUS, MODELS, cache_dir=tmp_path, **made)

    assert caught.value.status_code == status
    assert caught.value.busy is busy
    assert ("try again in a few minutes" in str(caught.value)) is busy


@pytest.mark.parametrize(
    "asked",
    [
        {"top": -1},  # would never reach the cut, and judge the whole corpus
        {"per_employer": -1},
        {"style": "vibes"},
        {"judge": "astrology"},
    ],
)
def test_a_request_that_makes_no_sense_is_refused_before_anything_is_paid(asked):
    with pytest.raises(ValueError):
        MatchRequest(cv=SANNE, **asked)
