"""CacheboxAdapter / CachetoolsAdapter behavior."""

from __future__ import annotations

import time

import pytest

cachebox = pytest.importorskip("cachebox")
cachetools = pytest.importorskip("cachetools")

from freshcache.adapters.cachebox import CacheboxAdapter  # noqa: E402
from freshcache.adapters.cachetools import CachetoolsAdapter  # noqa: E402


@pytest.fixture(params=[CacheboxAdapter, CachetoolsAdapter], ids=["cachebox", "cachetools"])
def adapter(request):
    return request.param()


def test_set_and_get(adapter):
    adapter.set("k", "v")
    assert adapter.get("k") == "v"


def test_get_missing(adapter):
    assert adapter.get("nope") is None
    assert adapter.get("nope", default="x") == "x"


def test_get_strict_raises(adapter):
    with pytest.raises(KeyError):
        adapter.get_strict("nope")


def test_delete(adapter):
    adapter.set("k", 1)
    assert adapter.delete("k") is True
    assert adapter.delete("k") is False


def test_add(adapter):
    assert adapter.add("k", 1) is True
    assert adapter.add("k", 2) is False
    assert adapter.get("k") == 1


def test_envelope_with_soft_ttl(adapter):
    adapter.set("k", 42, soft_ttl=10.0)
    env = adapter.get_envelope("k")
    assert env is not None
    assert env.value == 42


def test_no_envelope_without_soft_ttl(adapter):
    adapter.set("k", 42)
    assert adapter.get_envelope("k") is None


def test_hard_ttl_expires(adapter):
    adapter.set("k", 1, ttl=0.05)
    time.sleep(0.1)
    assert adapter.get("k") is None
