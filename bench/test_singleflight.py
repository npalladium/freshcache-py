"""Microbenchmarks for SingleFlight (sync).

The factory is a cheap lambda so the bench reflects single-flight + cache
overhead, not factory cost.
"""

from __future__ import annotations

import time

from pytest_benchmark.fixture import BenchmarkFixture

from freshcache.memory import MemoryCache
from freshcache.singleflight import SingleFlight, StalePolicy


def test_sf_fresh_hit(benchmark: BenchmarkFixture) -> None:
    cache = MemoryCache(maxsize=1024)
    sf = SingleFlight[str, int](cache)
    sf.get_or_create("k", lambda: 1, ttl=60.0, soft_ttl=30.0)

    def do() -> int:
        return sf.get_or_create("k", lambda: 1, ttl=60.0, soft_ttl=30.0)

    benchmark(do)


def test_sf_stale_serve(benchmark: BenchmarkFixture) -> None:
    cache = MemoryCache(maxsize=1024)
    sf = SingleFlight[str, int](cache)
    sf.get_or_create("k", lambda: 1, ttl=600.0, soft_ttl=0.0)
    # Soft TTL of 0 means every read sees a stale envelope.
    time.sleep(0.001)

    def do() -> int:
        return sf.get_or_create(
            "k", lambda: 1, ttl=600.0, soft_ttl=0.0, stale=StalePolicy.SERVE
        )

    benchmark(do)


def test_sf_stale_revalidate_backed_off(benchmark: BenchmarkFixture) -> None:
    # Wedge the regeneration set so REVALIDATE skips the executor.submit
    # path — we measure the steady-state stale-while-revalidate read, not
    # the cost of scheduling a background job.
    cache = MemoryCache(maxsize=1024)
    sf = SingleFlight[str, int](cache)
    sf.get_or_create("k", lambda: 1, ttl=600.0, soft_ttl=0.0)
    time.sleep(0.001)
    sf._regenerating.add("k")  # pyright: ignore[reportPrivateUsage]

    def do() -> int:
        return sf.get_or_create(
            "k", lambda: 1, ttl=600.0, soft_ttl=0.0, stale=StalePolicy.REVALIDATE
        )

    benchmark(do)
