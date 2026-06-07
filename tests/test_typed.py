"""TypedView roundtrip behavior."""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from freshcache import JsonCodec, MemoryCache, PickleCodec, TypedView


@dataclass(frozen=True)
class User:
    name: str
    age: int


def test_set_get_roundtrip_pickle():
    backend = MemoryCache()
    users: TypedView[str, User] = TypedView(backend, namespace="users")
    users.set("alice", User("Alice", 30))
    got = users.get("alice")
    assert got == User("Alice", 30)


def test_get_returns_none_on_miss():
    backend = MemoryCache()
    users: TypedView[str, User] = TypedView(backend, namespace="users")
    assert users.get("nope") is None


def test_get_strict_raises():
    backend = MemoryCache()
    users: TypedView[str, User] = TypedView(backend, namespace="users")
    with pytest.raises(KeyError):
        users.get_strict("nope")


def test_delete():
    backend = MemoryCache()
    users: TypedView[str, User] = TypedView(backend, namespace="users")
    users.set("alice", User("A", 1))
    assert users.delete("alice") is True
    assert users.get("alice") is None


def test_json_codec_roundtrip():
    backend = MemoryCache()
    cfg: TypedView[str, dict[str, int]] = TypedView(
        backend, namespace="cfg", codec=JsonCodec()
    )
    cfg.set("a", {"x": 1, "y": 2})
    assert cfg.get("a") == {"x": 1, "y": 2}


def test_namespace_isolation():
    backend = MemoryCache()
    a: TypedView[str, int] = TypedView(backend, namespace="ns_a", codec=JsonCodec())
    b: TypedView[str, int] = TypedView(backend, namespace="ns_b", codec=JsonCodec())
    a.set("k", 1)
    b.set("k", 2)
    assert a.get("k") == 1
    assert b.get("k") == 2


def test_pickle_codec_explicit():
    backend = MemoryCache()
    v: TypedView[str, User] = TypedView(
        backend, namespace="users", codec=PickleCodec()
    )
    v.set("x", User("X", 99))
    assert v.get("x") == User("X", 99)


def test_get_envelope_decodes():
    backend = MemoryCache()
    users: TypedView[str, User] = TypedView(backend, namespace="users")
    users.set("alice", User("Alice", 30), soft_ttl=100.0)
    env = users.get_envelope("alice")
    assert env is not None
    assert env.value == User("Alice", 30)
    assert env.soft_ttl == 100.0


def test_get_envelope_none_without_soft_ttl():
    backend = MemoryCache()
    users: TypedView[str, User] = TypedView(backend, namespace="users")
    users.set("alice", User("Alice", 30))  # no soft_ttl
    assert users.get_envelope("alice") is None
