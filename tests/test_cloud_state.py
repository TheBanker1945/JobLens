"""The nightly job's disk in a bucket (7.8.5): only vacancy state goes either way.

A fake bucket (httpx.MockTransport) stands in for Cloud Storage's JSON API,
so these run without a network or an account.
"""

import json
from urllib.parse import unquote

import httpx
import pytest

from joblens.cloud.state import PREFIX, BucketState, allowed, md5


@pytest.mark.parametrize(
    ("path", "ok"),
    [
        ("vacancies/indeed.jsonl", True),
        ("extracted/recruitee.jsonl", True),
        ("sightings.json", True),
        ("fetch-state.json", True),
        ("runs/2026-09-29_0300_fetch.json", True),
        ("cv/mohammed.pdf", False),  # a real CV: never
        ("cv-labels/mohammed.json", False),
        ("cv-runs/2026-09-22_run.json", False),
        ("runs/2026-09-22_match.json", False),  # only fetch reports
        ("vacancies/../cv/mohammed.pdf", False),
        ("vacancies/nested/indeed.jsonl", False),
        ("RealCVLogs.md", False),
        ("", False),
    ],
)
def test_only_vacancy_state_is_allowed(path, ok):
    assert allowed(path) is ok


class FakeBucket:
    """Enough of the JSON API: list, download, upload."""

    def __init__(self, objects: dict[str, bytes]):
        self.objects = objects
        self.uploads: list[str] = []
        self.tokens: list[str] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.tokens.append(request.headers.get("authorization", ""))
        path = request.url.path
        if request.method == "POST" and path.startswith("/upload/"):
            name = request.url.params["name"]
            self.objects[name] = request.content
            self.uploads.append(name)
            return httpx.Response(200, json={"name": name})
        if request.url.params.get("alt") == "media":
            name = unquote(path.split("/o/", 1)[1])
            return httpx.Response(200, content=self.objects[name])
        prefix = request.url.params.get("prefix", "")
        items = [
            {"name": name, "md5Hash": md5(data)}
            for name, data in self.objects.items()
            if name.startswith(prefix)
        ]
        return httpx.Response(200, json={"items": items})


def state_for(bucket: FakeBucket, root) -> BucketState:
    client = httpx.Client(transport=httpx.MockTransport(bucket))
    return BucketState("joblens-state", root, token=lambda: "t0ken", client=client)


def test_the_state_comes_down_and_a_stray_object_does_not(tmp_path):
    bucket = FakeBucket(
        {
            PREFIX + "vacancies/indeed.jsonl": b'{"source": "indeed"}\n',
            PREFIX + "sightings.json": json.dumps({"a": 1}).encode(),
            PREFIX + "cv/somebody.pdf": b"%PDF",  # should never be there
        }
    )

    count = state_for(bucket, tmp_path).download()

    assert count == 2
    assert (
        tmp_path / "vacancies" / "indeed.jsonl"
    ).read_bytes() == b'{"source": "indeed"}\n'
    assert not (tmp_path / "cv").exists()
    assert set(bucket.tokens) == {"Bearer t0ken"}


def test_only_changed_state_goes_up_and_never_a_cv(tmp_path):
    same = b'{"source": "greenhouse"}\n'
    bucket = FakeBucket({PREFIX + "vacancies/greenhouse.jsonl": same})
    (tmp_path / "vacancies").mkdir()
    (tmp_path / "vacancies" / "greenhouse.jsonl").write_bytes(same)  # unchanged
    (tmp_path / "vacancies" / "indeed.jsonl").write_bytes(b'{"source": "indeed"}\n')
    (tmp_path / "fetch-state.json").write_text("{}")
    (tmp_path / "cv").mkdir()
    (tmp_path / "cv" / "mohammed.pdf").write_bytes(b"%PDF")  # never goes up

    sent = state_for(bucket, tmp_path).upload()

    assert sent == ["fetch-state.json", "vacancies/indeed.jsonl"]
    assert bucket.uploads == [PREFIX + one for one in sent]
    assert not any("cv/" in name for name in bucket.objects)
