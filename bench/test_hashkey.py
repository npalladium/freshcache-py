"""Microbenchmarks for the default key function.

Validates the no-kwargs fast path: skipping `sorted()` + tuple wrapping when
kwargs is empty should be measurably cheaper than the kwargs path.
"""

from __future__ import annotations

from pytest_benchmark.fixture import BenchmarkFixture

from freshcache.decorators import hashkey


def test_hashkey_no_args(benchmark: BenchmarkFixture) -> None:
    benchmark(hashkey)


def test_hashkey_positional_only(benchmark: BenchmarkFixture) -> None:
    benchmark(hashkey, 1, 2, 3)


def test_hashkey_positional_wide(benchmark: BenchmarkFixture) -> None:
    benchmark(hashkey, 1, 2, 3, 4, 5, 6, 7, 8)


def test_hashkey_kwargs_small(benchmark: BenchmarkFixture) -> None:
    # Triggers the sorted-kwargs path.
    benchmark(lambda: hashkey(a=1, b=2))


def test_hashkey_kwargs_wide(benchmark: BenchmarkFixture) -> None:
    benchmark(lambda: hashkey(a=1, b=2, c=3, d=4, e=5, f=6))


def test_hashkey_mixed(benchmark: BenchmarkFixture) -> None:
    benchmark(lambda: hashkey(1, 2, a=1, b=2))
