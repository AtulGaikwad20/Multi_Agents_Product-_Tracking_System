import time

from app.playground.store import MemoryStore
from app.playground.supervisor import Supervisor
from tests.playground.fakes import FakeFetcher, FakeLlm, FakeSearch


def make_supervisor(search=None, fetcher=None, llm=None, settings=None):
    return Supervisor(search or FakeSearch(), fetcher or FakeFetcher(), llm or FakeLlm(), MemoryStore(),
                      settings or {"MAX_AGENTS_PER_RUN": 12})


def wait(run, timeout=15):
    end = time.time() + timeout
    while time.time() < end:
        if run.status in ("done", "error", "cancelled") and run.log.closed:
            return run
        time.sleep(0.05)
    raise AssertionError(f"run did not finish: {run.status}")
