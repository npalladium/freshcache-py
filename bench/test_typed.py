"""Microbenchmarks for TypedView.

Exercises the codec roundtrip and the cached-prefix key encoder.
"""

from __future__ import annotations

from pytest_benchmark.fixture import BenchmarkFixture

from freshcache.codecs import JsonCodec, PickleCodec
from freshcache.memory import MemoryCache
from freshcache.typed import TypedView

# Explicit dict[str, object] to keep TypedView's invariant V from rejecting
# the literal at the call site.
_SMALL: dict[str, object] = {"id": 42, "name": "alice", "active": True}


def _view_pickle() -> TypedView[str, dict[str, object]]:
    return TypedView(MemoryCache(maxsize=1024), namespace="bench", codec=PickleCodec())


def _view_json() -> TypedView[str, dict[str, object]]:
    return TypedView(MemoryCache(maxsize=1024), namespace="bench", codec=JsonCodec())


def test_typedview_k_encoding(benchmark: BenchmarkFixture) -> None:
    # Validates the cached self._prefix optimization.
    v = _view_pickle()
    benchmark(v._k, "some-key")  # pyright: ignore[reportPrivateUsage]


def test_typedview_set_pickle(benchmark: BenchmarkFixture) -> None:
    v = _view_pickle()
    benchmark(v.set, "k", _SMALL)


def test_typedview_get_hit_pickle(benchmark: BenchmarkFixture) -> None:
    v = _view_pickle()
    v.set("k", _SMALL)
    benchmark(v.get, "k")


def test_typedview_set_json(benchmark: BenchmarkFixture) -> None:
    v = _view_json()
    benchmark(v.set, "k", _SMALL)


def test_typedview_get_hit_json(benchmark: BenchmarkFixture) -> None:
    v = _view_json()
    v.set("k", _SMALL)
    benchmark(v.get, "k")


def test_typedview_get_miss(benchmark: BenchmarkFixture) -> None:
    v = _view_pickle()
    benchmark(v.get, "absent")
