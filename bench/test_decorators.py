"""Microbenchmarks for the @cached decorator (sync and async).

Validates the per-hit overhead after removing the counters lock. Covers
both the simple get/miss/set path (no soft_ttl) and the SingleFlight path
(soft_ttl set).
"""

from __future__ import annotations

import asyncio

from pytest_benchmark.fixture import BenchmarkFixture

from freshcache.decorators import cached
from freshcache.memory import MemoryCache


# ----- sync -----


def test_cached_sync_hit_no_soft_ttl(benchmark: BenchmarkFixture) -> None:
    cache = MemoryCache(maxsize=1024)

    @cached(cache)
    def f(x: int) -> int:
        return x * 2

    f(1)  # warm
    benchmark(f, 1)


def test_cached_sync_miss_no_soft_ttl(benchmark: BenchmarkFixture) -> None:
    cache = MemoryCache(maxsize=100_000)
    counter = [0]

    @cached(cache)
    def f(x: int) -> int:
        return x * 2

    def do() -> None:
        counter[0] += 1
        f(counter[0])

    benchmark(do)


def test_cached_sync_hit_with_soft_ttl(benchmark: BenchmarkFixture) -> None:
    # Goes through SingleFlight.get_or_create_detailed (fresh-envelope path).
    cache = MemoryCache(maxsize=1024)

    @cached(cache, soft_ttl=60.0)
    def f(x: int) -> int:
        return x * 2

    f(1)
    benchmark(f, 1)


# ----- async -----
#
# Each iteration pays one loop.run_until_complete dispatch (~30 µs on this
# machine), which dwarfs the cached-path work. Treat these numbers as a
# *relative* signal between async configurations, not an absolute cost of
# the @cached overhead.


def test_cached_async_hit_no_soft_ttl(
    benchmark: BenchmarkFixture, event_loop: asyncio.AbstractEventLoop
) -> None:
    cache = _AsyncMemAdapter()

    @cached(cache)
    async def f(x: int) -> int:
        return x * 2

    event_loop.run_until_complete(f(1))  # warm
    benchmark(lambda: event_loop.run_until_complete(f(1)))


def test_cached_async_hit_with_soft_ttl(
    benchmark: BenchmarkFixture, event_loop: asyncio.AbstractEventLoop
) -> None:
    cache = _AsyncMemAdapter()

    @cached(cache, soft_ttl=60.0)
    async def f(x: int) -> int:
        return x * 2

    event_loop.run_until_complete(f(1))
    benchmark(lambda: event_loop.run_until_complete(f(1)))


# ----- helpers -----


class _AsyncMemAdapter:
    """Thin async wrapper over MemoryCache so we can bench async @cached
    without pulling in the redis adapter (and a network round-trip)."""

    def __init__(self) -> None:
        self._inner = MemoryCache(maxsize=1024)

    async def get(self, key, /, *, default=None):  # type: ignore[no-untyped-def]
        return self._inner.get(key, default=default)

    async def get_strict(self, key, /):  # type: ignore[no-untyped-def]
        return self._inner.get_strict(key)

    async def set(self, key, value, /, *, ttl=None, soft_ttl=None):  # type: ignore[no-untyped-def]
        self._inner.set(key, value, ttl=ttl, soft_ttl=soft_ttl)

    async def delete(self, key, /):  # type: ignore[no-untyped-def]
        return self._inner.delete(key)

    async def get_envelope(self, key, /):  # type: ignore[no-untyped-def]
        return self._inner.get_envelope(key)
