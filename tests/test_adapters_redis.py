"""RedisAdapter behavior tests (fakeredis-backed)."""

from __future__ import annotations

import time

import pytest

fakeredis = pytest.importorskip("fakeredis")

from dataclasses import dataclass  # noqa: E402

from freshcache import TypedView  # noqa: E402
from freshcache.adapters.redis import RedisAdapter  # noqa: E402


@dataclass(frozen=True)
class KeyDC:
    a: str
    b: int


@pytest.fixture
def adapter():
    client = fakeredis.FakeRedis()
    return RedisAdapter(client, namespace="t")


def test_set_and_get_bytes_roundtrip(adapter):
    adapter.set("k", b"hello")
    assert adapter.get("k") == b"hello"


def test_get_missing_returns_default(adapter):
    assert adapter.get("nope") is None
    assert adapter.get("nope", default=b"x") == b"x"


def test_get_strict_raises(adapter):
    with pytest.raises(KeyError):
        adapter.get_strict("nope")


def test_set_requires_bytes(adapter):
    with pytest.raises(TypeError):
        adapter.set("k", "string-not-bytes")


def test_delete(adapter):
    adapter.set("k", b"v")
    assert adapter.delete("k") is True
    assert adapter.delete("k") is False


def test_envelope_only_with_soft_ttl(adapter):
    adapter.set("k", b"v")
    assert adapter.get_envelope("k") is None

    adapter.set("k2", b"v", soft_ttl=10.0)
    env = adapter.get_envelope("k2")
    assert env is not None
    assert env.value == b"v"
    assert env.soft_ttl == 10.0


def test_add_first_writer_wins(adapter):
    assert adapter.add("k", b"a") is True
    assert adapter.add("k", b"b") is False
    assert adapter.get("k") == b"a"


def test_hard_ttl_expires(adapter):
    adapter.set("k", b"v", ttl=0.05)
    time.sleep(0.1)
    assert adapter.get("k") is None


def test_typed_view_over_redis(adapter):
    users: TypedView[str, dict[str, int]] = TypedView(adapter, namespace="users")
    users.set("a", {"x": 1})
    assert users.get("a") == {"x": 1}


def test_typed_view_get_envelope_decodes(adapter):
    users: TypedView[str, dict[str, int]] = TypedView(adapter, namespace="users")
    users.set("a", {"x": 1}, soft_ttl=10.0)
    env = users.get_envelope("a")
    assert env is not None
    assert env.value == {"x": 1}


# ----- key encoding -----


def test_safe_string_keys_passthrough(adapter):
    """Plain ASCII string keys appear in Redis verbatim — readable in redis-cli."""
    adapter.set("alice", b"v")
    keys = list(adapter._c.scan_iter("t:*"))
    assert b"t:alice" in keys


def test_unsafe_string_keys_are_hashed(adapter):
    """Strings with control chars / unicode get hashed."""
    adapter.set("alice\nbob", b"v")
    keys = list(adapter._c.scan_iter("t:*"))
    assert any(k.startswith(b"t:#h:") for k in keys)
    assert b"t:alice\nbob" not in keys


def test_long_string_keys_are_hashed(adapter):
    adapter.set("a" * 500, b"v")
    keys = list(adapter._c.scan_iter("t:*"))
    assert any(k.startswith(b"t:#h:") for k in keys)


def test_int_key_hashes_stably(adapter):
    adapter.set(42, b"v")
    assert adapter.get(42) == b"v"


def test_tuple_key_hashes_stably(adapter):
    adapter.set(("u", 1, "x"), b"v")
    assert adapter.get(("u", 1, "x")) == b"v"
    assert adapter.get(("u", 1, "y")) is None  # different key


def test_dataclass_key_hashes_stably(adapter):
    adapter.set(KeyDC("x", 1), b"v")
    assert adapter.get(KeyDC("x", 1)) == b"v"
    assert adapter.get(KeyDC("x", 2)) is None


def test_custom_key_serializer():
    """User can fully override key encoding."""
    client = fakeredis.FakeRedis()
    from freshcache.adapters.redis import RedisAdapter

    adapter = RedisAdapter(client, namespace="custom", key_serializer=lambda k: f"by:{k}")
    adapter.set("alice", b"v")
    assert adapter.get("alice") == b"v"
    keys = list(client.scan_iter("custom:*"))
    assert b"custom:by:alice" in keys


def test_curly_braces_not_passthrough(adapter):
    """{} are reserved by Redis cluster for hashtags — never let them through."""
    adapter.set("{shard1}:alice", b"v")
    keys = list(adapter._c.scan_iter("t:*"))
    assert b"t:{shard1}:alice" not in keys
    assert any(k.startswith(b"t:#h:") for k in keys)


def test_unpickleable_key_raises():
    """Keys that pickle.dumps cannot handle should fail clearly."""
    from freshcache.adapters.redis import default_key_suffix

    class Weird:
        def __hash__(self):
            return 0

        def __reduce__(self):
            raise TypeError("can't pickle me")

    with pytest.raises(TypeError, match="cannot be serialized"):
        default_key_suffix(Weird())
