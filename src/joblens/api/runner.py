"""Where background jobs run: a couple of threads in the server process.

By hand, the smallest thing that works: a thread pool. A match is mostly
waiting on a model provider, which a thread does well. Two at a time, because a
tester phase has a handful of people and each match already runs six judge
calls in parallel (cv/judge.py WORKERS).

What this does not do, on purpose: survive a restart (the job row is marked
interrupted instead: Database.fail_interrupted_jobs), or spread work over
several servers. A queue service (Cloud Tasks, a Redis queue) does both, and
earns its place when there is more than one server -- hosting, 7.8.
"""

from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from typing import Protocol

WORKERS = 2


class Runner(Protocol):
    def submit(self, work: Callable[..., None], *args, **kwargs) -> None: ...

    def shutdown(self) -> None: ...


class ThreadRunner:
    def __init__(self, workers: int = WORKERS):
        self._pool = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="job")

    def submit(self, work: Callable[..., None], *args, **kwargs) -> None:
        self._pool.submit(work, *args, **kwargs)

    def shutdown(self) -> None:
        self._pool.shutdown(wait=False, cancel_futures=True)


class InlineRunner:
    """Runs a job before `submit` returns: for tests, where waiting is flaky."""

    def submit(self, work: Callable[..., None], *args, **kwargs) -> None:
        work(*args, **kwargs)

    def shutdown(self) -> None:
        pass
