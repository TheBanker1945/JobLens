"""Starting and stopping the nightly job from the admin page (7.10.2).

The Cloud Run Admin API over httpx, with the token the metadata server hands
the app's own service account -- as the job reads its bucket (cloud/state.py),
so no new dependency. That account may do exactly this and nothing more:
`roles/run.jobsExecutorWithOverrides` on this one job (run it, run it with
arguments, cancel an execution of it).

**Start** runs the job with the arguments given; they replace the job's own,
and it has none (scripts/nightly.py is the image's ENTRYPOINT). **Stop**
cancels an execution by the name the job wrote down for itself
(CLOUD_RUN_EXECUTION): the job keeps its bucket unchanged until the very end,
so a stopped run leaves nothing half-written behind.
"""

import os
import re
from collections.abc import Callable, Mapping

import httpx

from joblens.cloud.state import metadata_token

API = "https://run.googleapis.com/v2"
# projects/{project}/locations/{region}/jobs/{job}
JOB = re.compile(r"^projects/[a-z0-9-]+/locations/[a-z0-9-]+/jobs/[a-z0-9-]+$")
EXECUTION = re.compile(r"^[a-z0-9-]{1,63}$")  # "joblens-nightly-rw742"


class NightlyJob:
    def __init__(self, name: str, *, token: Callable[[], str], client: httpx.Client):
        if not JOB.match(name):
            raise ValueError(
                f"not a Cloud Run job name: {name!r} "
                "(projects/PROJECT/locations/REGION/jobs/JOB)"
            )
        self.name = name
        self.token = token
        self.client = client

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "NightlyJob | None":
        """From JOBLENS_NIGHTLY_JOB; None where it is not set (this machine)."""
        name = (env if env is not None else os.environ).get("JOBLENS_NIGHTLY_JOB")
        if not name:
            return None
        client = httpx.Client(timeout=30)
        return cls(name, token=lambda: metadata_token(client), client=client)

    def _post(self, url: str, body: dict) -> None:
        answer = self.client.post(
            url, json=body, headers={"Authorization": f"Bearer {self.token()}"}
        )
        answer.raise_for_status()

    def start(self, args: list[str]) -> None:
        """A new execution, with these arguments to scripts/nightly.py."""
        self._post(
            f"{API}/{self.name}:run",
            {"overrides": {"containerOverrides": [{"args": args}]}},
        )

    def stop(self, execution: str) -> None:
        """Cancel one execution of this job, by its short name."""
        if not EXECUTION.match(execution):
            raise ValueError(f"not an execution name: {execution!r}")
        self._post(f"{API}/{self.name}/executions/{execution}:cancel", {})


def refusal(err: httpx.HTTPError) -> str:
    """Cloud Run's own sentence for a refused call, for the owner to read."""
    if isinstance(err, httpx.HTTPStatusError):
        try:
            message = err.response.json()["error"]["message"]
        except (ValueError, KeyError, TypeError):
            message = err.response.text[:200]
        return f"Cloud Run said {err.response.status_code}: {message}"
    return f"Cloud Run could not be reached ({type(err).__name__})."
