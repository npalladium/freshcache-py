"""@cached decorator behavior."""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from freshcache import MemoryCache, StalePolicy, TypedView, cached


@dataclass(frozen=True)
class User:
    name: str


def test_sync_cached_returns_cached_value():
    backend = MemoryCache()
    users: TypedView[str, User] = TypedView(backend, namespace="users")

    calls = 0

    @cached(cache=users)
    def get_user(uid: str) -> User:
        nonlocal calls
        calls += 1
        return User(uid)

    assert get_user("a") == User("a")
    assert get_user("a") == User("a")
    assert calls == 1


def test_sync_cache_info_counts():
    backend = MemoryCache()
    users: TypedView[str, User] = TypedView(backend, namespace="users")

    @cached(cache=users)
    def get_user(uid: str) -> User:
        return User(uid)

    get_user("a")  # miss
    get_user("a")  # hit
    get_user("b")  # miss
    info = get_user.cache_info()
    assert info.hits == 1
    assert info.misses == 2


def test_cache_clear_resets_counters():
    backend = MemoryCache()
    users: TypedView[str, User] = TypedView(backend, namespace="users")

    @cached(cache=users)
    def f(uid: str) -> User:
        return User(uid)

    f("a")
    f("a")
    f.cache_clear()
    info = f.cache_info()
    assert info.hits == 0 and info.misses == 0


def test_sync_async_mismatch_raises():
    backend = MemoryCache()
    # Pass as `Any` to silence the type checker — we're testing the runtime
    # detection here, since runtime_checkable Protocols don't catch this.
    users_untyped: object = TypedView(backend, namespace="users")

    with pytest.raises(TypeError, match="sync/async mismatch"):

        @cached(cache=users_untyped)  # pyright: ignore[reportArgumentType]
        async def _async_over_sync(uid: str) -> User:
            return User(uid)


def test_soft_ttl_enables_singleflight_path():
    backend = MemoryCache()
    users: TypedView[str, User] = TypedView(backend, namespace="users")

    calls = 0

    @cached(cache=users, soft_ttl=10.0)
    def get_user(uid: str) -> User:
        nonlocal calls
        calls += 1
        return User(uid)

    get_user("a")
    get_user("a")
    assert calls == 1


def test_stale_serve_returns_old_value_without_recompute():
    import time

    backend = MemoryCache()
    users: TypedView[str, User] = TypedView(backend, namespace="users")

    calls = 0

    @cached(cache=users, soft_ttl=0.01, stale=StalePolicy.SERVE)
    def get_user(uid: str) -> User:
        nonlocal calls
        calls += 1
        return User(uid + str(calls))

    first = get_user("a")
    assert first.name == "a1"
    time.sleep(0.03)
    second = get_user("a")
    assert second.name == "a1"  # stale serve
    assert calls == 1


@pytest.mark.asyncio
async def test_async_cached_returns_cached_value():
    class _AsyncMem:
        """Minimal async cache for the test (wraps MemoryCache)."""

        def __init__(self) -> None:
            self._m = MemoryCache()

        async def get(self, key, /, *, default=None):
            return self._m.get(key, default=default)

        async def get_strict(self, key, /):
            return self._m.get_strict(key)

        async def set(self, key, value, /, *, ttl=None, soft_ttl=None):
            self._m.set(key, value, ttl=ttl, soft_ttl=soft_ttl)

        async def delete(self, key, /):
            return self._m.delete(key)

        async def get_envelope(self, key, /):
            return self._m.get_envelope(key)

    am = _AsyncMem()
    calls = 0

    @cached(cache=am)
    async def get_user(uid: str) -> User:
        nonlocal calls
        calls += 1
        return User(uid)

    assert await get_user("a") == User("a")
    assert await get_user("a") == User("a")
    assert calls == 1
