"""Type-checker spike. This file exercises the riskiest combinations.

Run:
    uv run mypy tests/test_types.py
    uv run pyright tests/test_types.py

Lines marked ``EXPECT-ERROR`` should produce a type-check error.
Lines marked ``EXPECT-OK`` should pass cleanly.
``reveal_type(...)`` calls show what each checker infers.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, assert_type

from freshcache import (
    Cache,
    MemoryCache,
    SupportsAdd,
    SupportsEnvelope,
    TypedCache,
    TypedView,
    cached,
)


@dataclass(frozen=True)
class User:
    name: str
    age: int


# ----- runtime_checkable isinstance() -----

backend: Cache = MemoryCache(maxsize=128)

assert isinstance(backend, Cache)              # EXPECT-OK
assert isinstance(backend, SupportsAdd)        # EXPECT-OK (MemoryCache has add)
assert isinstance(backend, SupportsEnvelope)   # EXPECT-OK (MemoryCache has get_envelope)


# ----- Cache (untyped) reads return Any -----

raw = backend.get("foo")
assert_type(raw, Any)                          # Cache.get returns Any

raw_default = backend.get("foo", default="DEFAULT")
# raw_default is Any


# ----- TypedView gives static V typing -----

users: TypedCache[str, User] = TypedView(backend, namespace="users")

users.set("alice", User(name="Alice", age=30))            # EXPECT-OK

maybe_user = users.get("alice")
if TYPE_CHECKING:
    assert_type(maybe_user, "User | None")                 # EXPECT-OK


user_or_guest = users.get("alice", default=User(name="guest", age=0))
if TYPE_CHECKING:
    assert_type(user_or_guest, User)                       # EXPECT-OK: default same as V → V


# Caller-supplied sentinel of different type
class _MissingT:
    pass


MISSING = _MissingT()

user_or_missing = users.get("alice", default=MISSING)
if TYPE_CHECKING:
    assert_type(user_or_missing, "User | _MissingT")       # EXPECT-OK: V | T overload


strict_user = users.get_strict("alice")
if TYPE_CHECKING:
    assert_type(strict_user, User)                         # EXPECT-OK


# ----- Setting wrong V should fail -----

# EXPECT-ERROR: int is not User
users.set("alice", 42)  # type: ignore[arg-type]


# ----- @cached return-type matching -----


@cached(cache=users, ttl=3600)
def get_user(user_id: str) -> User:                        # EXPECT-OK
    return User(name=user_id, age=0)


# EXPECT-ERROR: return type float, but cache's V is User
@cached(cache=users, ttl=3600)  # type: ignore[arg-type]
def get_score(user_id: str) -> float:
    return 0.0


# ----- @cached with untyped Cache returns Any -----


@cached(cache=backend, ttl=3600)
def get_anything(user_id: str) -> dict[str, int]:          # EXPECT-OK
    return {user_id: 0}


# ----- Multiple TypedViews over one backend -----

scores: TypedCache[tuple[str, int], float] = TypedView(backend, namespace="scores")
configs: TypedCache[str, dict[str, object]] = TypedView(backend, namespace="cfg")

scores.set(("alice", 2026), 100.5)
configs.set("feature_flags", {"x": True})


# ----- Capability narrowing -----


def needs_add(c: Cache) -> None:
    if isinstance(c, SupportsAdd):
        # c is narrowed to Cache & SupportsAdd inside the branch
        ok = c.add("k", "v")
        if TYPE_CHECKING:
            assert_type(ok, bool)


needs_add(backend)


# ----- Hashable key bound -----

# K bound=Hashable. tuple[str, int] is Hashable → OK.
typed_tuple_keys: TypedCache[tuple[str, int], float] = TypedView(
    backend, namespace="x"
)
typed_tuple_keys.get(("a", 1))                             # EXPECT-OK


# ----- SingleFlight typing -----

from freshcache import SingleFlight, StalePolicy  # noqa: E402

sf: SingleFlight[str, User] = SingleFlight(backend)
maybe_sf_value: User = sf.get_or_create(
    "alice",
    lambda: User(name="Alice", age=30),
    ttl=60,
    soft_ttl=30,
    stale=StalePolicy.REVALIDATE,
)
if TYPE_CHECKING:
    assert_type(maybe_sf_value, User)


# ----- Codec composition typing -----

from freshcache import Codec, GzipCodec, JsonCodec, PickleCodec  # noqa: E402

base_codec: Codec = PickleCodec()
nested: Codec = GzipCodec(JsonCodec())                     # compose
double_nested: Codec = GzipCodec(GzipCodec(PickleCodec())) # ridiculous but should type-check

blob: bytes = nested.dumps({"k": 1})
restored: object = nested.loads(blob)


# ----- Async protocols (declaration-only, no runtime needed) -----

from freshcache import AsyncCache, AsyncTypedCache  # noqa: E402


async def use_async_cache(c: AsyncCache) -> None:
    raw = await c.get("foo", default="def")
    assert_type(raw, Any)


async def use_async_typed(c: AsyncTypedCache[str, User]) -> None:
    maybe = await c.get("alice")
    if TYPE_CHECKING:
        assert_type(maybe, "User | None")
    forced = await c.get("alice", default=User(name="g", age=0))
    if TYPE_CHECKING:
        assert_type(forced, User)
    strict = await c.get_strict("alice")
    if TYPE_CHECKING:
        assert_type(strict, User)
    await c.set("alice", User(name="A", age=1))
