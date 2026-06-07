"""MemoryCache behavior tests."""

from __future__ import annotations

import time

import pytest

from freshcache import Envelope, MemoryCache, NullCache


def test_get_missing_returns_default():
    c = MemoryCache()
    assert c.get("nope") is None
    assert c.get("nope", default="x") == "x"


def test_set_and_get_roundtrip():
    c = MemoryCache()
    c.set("k", 42)
    assert c.get("k") == 42


def test_get_strict_raises_on_miss():
    c = MemoryCache()
    with pytest.raises(KeyError):
        c.get_strict("nope")


def test_delete_returns_existence():
    c = MemoryCache()
    c.set("k", 1)
    assert c.delete("k") is True
    assert c.delete("k") is False


def test_lru_eviction_on_size_overflow():
    c = MemoryCache(maxsize=2)
    c.set("a", 1)
    c.set("b", 2)
    c.set("c", 3)
    assert c.get("a") is None
    assert c.get("b") == 2
    assert c.get("c") == 3


def test_lru_promotes_recently_read():
    c = MemoryCache(maxsize=2)
    c.set("a", 1)
    c.set("b", 2)
    c.get("a")  # promote a
    c.set("c", 3)  # should evict b
    assert c.get("a") == 1
    assert c.get("b") is None
    assert c.get("c") == 3


def test_hard_ttl_expires():
    c = MemoryCache()
    c.set("k", 1, ttl=0.05)
    assert c.get("k") == 1
    time.sleep(0.07)
    assert c.get("k") is None


def test_soft_ttl_envelope_when_provided():
    c = MemoryCache()
    c.set("k", 1, soft_ttl=10.0)
    env = c.get_envelope("k")
    assert env is not None
    assert env.value == 1
    assert env.soft_ttl == 10.0


def test_no_envelope_without_soft_ttl():
    c = MemoryCache()
    c.set("k", 1)
    assert c.get_envelope("k") is None


def test_envelope_is_stale_after_soft_ttl():
    env = Envelope(1, created_at=time.time() - 10, soft_ttl=1.0)
    assert env.is_stale()


def test_envelope_fresh_within_soft_ttl():
    env = Envelope(1, created_at=time.time(), soft_ttl=10.0)
    assert not env.is_stale()


def test_add_first_writer_wins():
    c = MemoryCache()
    assert c.add("k", 1) is True
    assert c.add("k", 2) is False
    assert c.get("k") == 1


def test_add_succeeds_after_expiry():
    c = MemoryCache()
    c.set("k", 1, ttl=0.05)
    time.sleep(0.07)
    assert c.add("k", 2) is True
    assert c.get("k") == 2


def test_info_counters():
    c = MemoryCache()
    c.set("k", 1)
    c.get("k")  # hit
    c.get("k")  # hit
    c.get("missing")  # miss
    info = c.info()
    assert info.hits == 2
    assert info.misses == 1
    assert info.size == 1
    assert info.maxsize == 1024


def test_clear_resets():
    c = MemoryCache()
    c.set("a", 1)
    c.get("a")
    c.clear()
    info = c.info()
    assert info.hits == 0 and info.misses == 0 and info.size == 0


def test_maxsize_validation():
    with pytest.raises(ValueError):
        MemoryCache(maxsize=0)


def test_null_cache_always_misses():
    n = NullCache()
    n.set("k", 1)
    assert n.get("k") is None
    assert n.get("k", default="x") == "x"
    assert n.get_envelope("k") is None
    assert n.delete("k") is False
    assert n.add("k", 1) is True  # no conflict
    info = n.info()
    assert info.hits == 0
    assert info.misses > 0


def test_null_cache_get_strict_raises():
    n = NullCache()
    with pytest.raises(KeyError):
        n.get_strict("k")
