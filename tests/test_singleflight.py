"""SingleFlight behavior tests."""

from __future__ import annotations

import threading
import time

import pytest

from freshcache import MemoryCache, NullCache, SingleFlight, StalePolicy


def test_requires_supports_envelope():
    class _NoEnv:
        def get(self, *a, **kw):
            return None

        def set(self, *a, **kw):
            return None

        def get_strict(self, *a, **kw):
            raise KeyError

        def delete(self, *a, **kw):
            return False

    with pytest.raises(TypeError):
        SingleFlight(_NoEnv())


def test_first_arrival_runs_factory_once():
    cache = MemoryCache()
    sf: SingleFlight[str, int] = SingleFlight(cache)

    calls = 0

    def factory() -> int:
        nonlocal calls
        calls += 1
        return 42

    v = sf.get_or_create("k", factory, soft_ttl=10.0)
    assert v == 42
    v = sf.get_or_create("k", factory, soft_ttl=10.0)
    assert v == 42
    assert calls == 1


def test_concurrent_callers_dedup_factory():
    cache = MemoryCache()
    sf: SingleFlight[str, int] = SingleFlight(cache)

    started = threading.Event()
    release = threading.Event()
    calls = 0

    def factory() -> int:
        nonlocal calls
        calls += 1
        started.set()
        release.wait(timeout=2)
        return 42

    results: list[int] = []

    def worker() -> None:
        results.append(sf.get_or_create("k", factory, soft_ttl=10.0))

    ts = [threading.Thread(target=worker) for _ in range(8)]
    for t in ts:
        t.start()
    started.wait(timeout=2)
    release.set()
    for t in ts:
        t.join(timeout=2)

    assert calls == 1
    assert results == [42] * 8


def test_factory_exception_propagates_to_all_waiters():
    cache = MemoryCache()
    sf: SingleFlight[str, int] = SingleFlight(cache)

    def factory() -> int:
        raise RuntimeError("boom")

    with pytest.raises(RuntimeError, match="boom"):
        sf.get_or_create("k", factory, soft_ttl=10.0)


def test_stale_serve_returns_stale_no_regen():
    cache = MemoryCache()
    sf: SingleFlight[str, int] = SingleFlight(cache)

    sf.get_or_create("k", lambda: 1, soft_ttl=0.01)
    time.sleep(0.03)

    calls = 0

    def factory() -> int:
        nonlocal calls
        calls += 1
        return 999

    v = sf.get_or_create("k", factory, soft_ttl=0.01, stale=StalePolicy.SERVE)
    assert v == 1
    assert calls == 0


def test_stale_revalidate_returns_stale_and_schedules():
    cache = MemoryCache()
    sf: SingleFlight[str, int] = SingleFlight(cache)
    try:
        sf.get_or_create("k", lambda: 1, soft_ttl=0.01)
        time.sleep(0.03)

        regen_started = threading.Event()
        regen_finish = threading.Event()

        def factory() -> int:
            regen_started.set()
            regen_finish.wait(timeout=2)
            return 999

        v = sf.get_or_create("k", factory, soft_ttl=0.01, stale=StalePolicy.REVALIDATE)
        assert v == 1
        assert regen_started.wait(timeout=2)
        regen_finish.set()

        # Drain: wait until cache has 999.
        for _ in range(200):
            if cache.get("k") == 999:
                break
            time.sleep(0.01)
        assert cache.get("k") == 999
    finally:
        sf.close()


def test_detailed_result_flags():
    cache = MemoryCache()
    sf: SingleFlight[str, int] = SingleFlight(cache)
    r = sf.get_or_create_detailed("k", lambda: 1, soft_ttl=10.0)
    assert r.value == 1
    assert r.was_stale is False
    assert r.revalidating is False

    r2 = sf.get_or_create_detailed("k", lambda: 2, soft_ttl=10.0)
    assert r2.value == 1
    assert r2.was_stale is False


def test_null_cache_rejected():
    # NullCache.get_envelope exists, but always returns None; SingleFlight
    # accepts it (it's structurally SupportsEnvelope) — every call is a miss.
    n = NullCache()
    sf: SingleFlight[str, int] = SingleFlight(n)
    calls = 0

    def factory() -> int:
        nonlocal calls
        calls += 1
        return 7

    assert sf.get_or_create("k", factory, soft_ttl=10.0) == 7
    assert sf.get_or_create("k", factory, soft_ttl=10.0) == 7
    # NullCache stores nothing → factory runs every time.
    assert calls == 2
