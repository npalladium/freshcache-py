"""Microbenchmarks for the in-memory transport cache."""

from __future__ import annotations

from pytest_benchmark.fixture import BenchmarkFixture

from freshcache.memory import MemoryCache


def test_memory_set_no_ttl(benchmark: BenchmarkFixture) -> None:
    c = MemoryCache(maxsize=10_000)
    counter = [0]

    def do() -> None:
        counter[0] += 1
        c.set(counter[0], "v")

    benchmark(do)


def test_memory_set_with_soft_ttl(benchmark: BenchmarkFixture) -> None:
    # Envelope construction path.
    c = MemoryCache(maxsize=10_000)
    counter = [0]

    def do() -> None:
        counter[0] += 1
        c.set(counter[0], "v", soft_ttl=60.0)

    benchmark(do)


def test_memory_get_hit(benchmark: BenchmarkFixture) -> None:
    c = MemoryCache(maxsize=1024)
    c.set("k", "v")
    benchmark(c.get, "k")


def test_memory_get_miss(benchmark: BenchmarkFixture) -> None:
    c = MemoryCache(maxsize=1024)
    benchmark(c.get, "absent")


def test_memory_get_envelope_hit(benchmark: BenchmarkFixture) -> None:
    c = MemoryCache(maxsize=1024)
    c.set("k", "v", soft_ttl=60.0)
    benchmark(c.get_envelope, "k")


def test_memory_delete(benchmark: BenchmarkFixture) -> None:
    # Re-populate per iteration so we measure actual deletion, not misses.
    c = MemoryCache(maxsize=10_000)

    def do() -> None:
        c.set("k", "v")
        c.delete("k")

    benchmark(do)
