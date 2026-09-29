"""The nightly job's disk: the vacancy state, kept in a Cloud Storage bucket (7.8.5).

A Cloud Run job starts with an empty disk every time. The fetch keeps its
state in files -- the stored vacancies, their extractions, when each vacancy
was last listed, which sites refused and until when -- and appends to them the
way it does on a laptop. So the job downloads that state from a bucket into
data/raw/, runs the pipeline unchanged, and uploads what changed.

**Only vacancy state, by an allow-list.** data/raw/ on a laptop also holds CVs,
labels and runs of real people. Nothing outside ALLOWED is ever downloaded or
uploaded, whatever the bucket or the disk holds -- so a CV cannot reach the
bucket by accident, and a stray object in the bucket cannot reach the job.

**No new dependency.** The bucket's JSON API over httpx, with the token the
Cloud Run metadata server hands the job's own service account.
"""

import base64
import hashlib
from collections.abc import Callable
from fnmatch import fnmatch
from pathlib import Path
from urllib.parse import quote

import httpx

PREFIX = "raw/"  # the objects live under raw/, mirroring data/raw/
ALLOWED = (
    "vacancies/*.jsonl",  # the stored adverts, one file per source
    "extracted/*.jsonl",  # what extraction read from them
    "sightings.json",  # when each was last listed: open or closed
    "fetch-state.json",  # which sites refused, and until when
    "runs/*_fetch.json",  # the reports of each fetch
)
METADATA_TOKEN = (
    "http://metadata.google.internal/computeMetadata/v1/"
    "instance/service-accounts/default/token"
)
API = "https://storage.googleapis.com/storage/v1/b"
UPLOAD = "https://storage.googleapis.com/upload/storage/v1/b"


def allowed(relative: str) -> bool:
    """Whether a path under data/raw/ is vacancy state (and nothing else)."""
    parts = relative.split("/")
    if any(part in ("", ".", "..") for part in parts):
        return False
    return any(
        len(parts) == len(pattern.split("/")) and fnmatch(relative, pattern)
        for pattern in ALLOWED
    )


def metadata_token(client: httpx.Client) -> str:
    """The job's service-account token, from the Cloud Run metadata server."""
    answer = client.get(METADATA_TOKEN, headers={"Metadata-Flavor": "Google"})
    answer.raise_for_status()
    return answer.json()["access_token"]


def md5(data: bytes) -> str:
    """As the bucket reports it: base64 of the MD5 digest."""
    return base64.b64encode(hashlib.md5(data).digest()).decode()


class BucketState:
    def __init__(
        self,
        bucket: str,
        root: Path,
        *,
        token: Callable[[], str],
        client: httpx.Client,
    ):
        self.bucket = bucket
        self.root = root
        self.token = token
        self.client = client

    def _auth(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.token()}"}

    def listing(self) -> dict[str, str]:
        """The allowed objects in the bucket, as {relative path: md5}."""
        found: dict[str, str] = {}
        page = None
        while True:
            params = {"prefix": PREFIX, "fields": "items(name,md5Hash),nextPageToken"}
            if page:
                params["pageToken"] = page
            answer = self.client.get(
                f"{API}/{self.bucket}/o", params=params, headers=self._auth()
            )
            answer.raise_for_status()
            body = answer.json()
            for item in body.get("items", []):
                relative = item["name"].removeprefix(PREFIX)
                if allowed(relative):
                    found[relative] = item.get("md5Hash", "")
            page = body.get("nextPageToken")
            if not page:
                return found

    def download(self) -> int:
        """Every allowed object into root/. Returns how many."""
        objects = self.listing()
        for relative in objects:
            name = quote(PREFIX + relative, safe="")
            answer = self.client.get(
                f"{API}/{self.bucket}/o/{name}",
                params={"alt": "media"},
                headers=self._auth(),
            )
            answer.raise_for_status()
            target = self.root / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(answer.content)
        return len(objects)

    def upload(self) -> list[str]:
        """Every allowed file in root/ the bucket does not hold as it is now."""
        remote = self.listing()
        sent = []
        for path in sorted(self.root.rglob("*")):
            relative = path.relative_to(self.root).as_posix()
            if not path.is_file() or not allowed(relative):
                continue
            data = path.read_bytes()
            if remote.get(relative) == md5(data):
                continue
            answer = self.client.post(
                f"{UPLOAD}/{self.bucket}/o",
                params={"uploadType": "media", "name": PREFIX + relative},
                content=data,
                headers=self._auth() | {"Content-Type": "application/octet-stream"},
            )
            answer.raise_for_status()
            sent.append(relative)
        return sent
