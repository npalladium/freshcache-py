"""@cached decorator.

Two overloads against a typed cache (static V-checking) and a bare Cache
(Any return). For coroutine functions, returns an async wrapper that uses
AsyncSingleFlight under the hood; for sync functions, SingleFlight.
"""

from __future__ import annotations

import inspect
import threading
from collections.abc import Awaitable, Callable, Hashable
from dataclasses import dataclass
from functools import wraps
from typing import Any, ParamSpec, Protocol, TypeVar, overload

from freshcache.async_singleflight import AsyncSingleFlight
from freshcache.protocols import AsyncCache, AsyncTypedCache, Cache, TypedCache
from freshcache.singleflight import SingleFlight, StalePolicy

P = ParamSpec("P")
K = TypeVar("K", bound=Hashable)
V = TypeVar("V")


@dataclass(frozen=True, slots=True)
class DecoratorInfo:
    """Decorator-side counters. Distinct from Cache.info() (backend-side)."""

    hits: int
    misses: int
    regens: int
    backoffs: int


class _Cached(Protocol[P, V]):
    """The wrapped callable, with .cache_info()."""

    __wrapped__: Callable[P, V]
    def __call__(self, *args: P.args, **kwargs: P.kwargs) -> V: ...
    def cache_info(self) -> DecoratorInfo: ...
    def cache_clear(self) -> None: ...


class _AsyncCached(Protocol[P, V]):
    __wrapped__: Callable[P, Awaitable[V]]
    def __call__(self, *args: P.args, **kwargs: P.kwargs) -> Awaitable[V]: ...
    def cache_info(self) -> DecoratorInfo: ...
    def cache_clear(self) -> None: ...


def hashkey(*args: Hashable, **kwargs: Hashable) -> Hashable:
    """Default key function: order-stable tuple of args + sorted kwargs."""
    if not kwargs:
        return args
    return (args, tuple(sorted(kwargs.items())))


# ----- overloads -----


@overload
def cached(
    cache: TypedCache[K, V],
    *,
    ttl: float | None = None,
    soft_ttl: float | None = None,
    stale: StalePolicy = StalePolicy.BLOCK,
    key: Callable[..., Hashable] = hashkey,
) -> Callable[[Callable[P, V]], _Cached[P, V]]: ...


@overload
def cached(
    cache: AsyncTypedCache[K, V],
    *,
    ttl: float | None = None,
    soft_ttl: float | None = None,
    stale: StalePolicy = StalePolicy.BLOCK,
    key: Callable[..., Hashable] = hashkey,
) -> Callable[[Callable[P, Awaitable[V]]], _AsyncCached[P, V]]: ...


@overload
def cached(
    cache: Cache,
    *,
    ttl: float | None = None,
    soft_ttl: float | None = None,
    stale: StalePolicy = StalePolicy.BLOCK,
    key: Callable[..., Hashable] = hashkey,
) -> Callable[[Callable[P, Any]], _Cached[P, Any]]: ...


@overload
def cached(
    cache: AsyncCache,
    *,
    ttl: float | None = None,
    soft_ttl: float | None = None,
    stale: StalePolicy = StalePolicy.BLOCK,
    key: Callable[..., Hashable] = hashkey,
) -> Callable[[Callable[P, Awaitable[Any]]], _AsyncCached[P, Any]]: ...


def cached(
    cache: Any,
    *,
    ttl: float | None = None,
    soft_ttl: float | None = None,
    stale: StalePolicy = StalePolicy.BLOCK,
    key: Callable[..., Hashable] = hashkey,
) -> Any:
    """See overloads. Decorator over a Cache/TypedCache (sync or async).

    Detects coroutine functions at decoration time and returns the matching
    wrapper. Mixing (e.g., async function over sync cache) raises TypeError.
    """

    def decorator(func: Callable[..., Any]) -> Any:
        is_coro = inspect.iscoroutinefunction(func)
        # runtime_checkable only verifies method presence, not async-ness.
        # Probe the .get coroutine status directly.
        get_method = getattr(cache, "get", None)
        is_async_cache = inspect.iscoroutinefunction(get_method)
        if is_coro != is_async_cache:
            raise TypeError(
                "cached: sync/async mismatch between function "
                f"({'async' if is_coro else 'sync'}) and cache "
                f"({'async' if is_async_cache else 'sync'})"
            )
        if is_coro:
            return _build_async(func, cache, ttl, soft_ttl, stale, key)
        return _build_sync(func, cache, ttl, soft_ttl, stale, key)

    return decorator


# ----- sync builder -----


class _Counters:
    __slots__ = ("hits", "misses", "regens", "backoffs", "lock")

    def __init__(self) -> None:
        self.hits = 0
        self.misses = 0
        self.regens = 0
        self.backoffs = 0
        self.lock = threading.Lock()

    def snapshot(self) -> DecoratorInfo:
        with self.lock:
            return DecoratorInfo(self.hits, self.misses, self.regens, self.backoffs)

    def reset(self) -> None:
        with self.lock:
            self.hits = 0
            self.misses = 0
            self.regens = 0
            self.backoffs = 0


def _build_sync(
    func: Callable[..., Any],
    cache: Any,
    ttl: float | None,
    soft_ttl: float | None,
    stale: StalePolicy,
    key_fn: Callable[..., Hashable],
) -> Any:
    counters = _Counters()
    has_envelope = hasattr(cache, "get_envelope")
    sf: SingleFlight[Hashable, Any] | None = None
    if has_envelope and soft_ttl is not None:
        sf = SingleFlight(cache)

    @wraps(func)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        k = key_fn(*args, **kwargs)
        if sf is not None:
            result = sf.get_or_create_detailed(
                k,
                lambda: func(*args, **kwargs),
                ttl=ttl,
                soft_ttl=soft_ttl,
                stale=stale,
            )
            if result.was_stale and result.revalidating:
                counters.regens += 1
            counters.hits += 1
            return result.value

        # No soft TTL → simple get / miss / set.
        _SENTINEL: Any = _MISS
        hit = cache.get(k, default=_SENTINEL)
        if hit is not _SENTINEL:
            counters.hits += 1
            return hit
        counters.misses += 1
        value = func(*args, **kwargs)
        cache.set(k, value, ttl=ttl, soft_ttl=soft_ttl)
        return value

    def cache_info() -> DecoratorInfo:
        return counters.snapshot()

    def cache_clear() -> None:
        counters.reset()

    wrapper.cache_info = cache_info  # type: ignore[attr-defined]
    wrapper.cache_clear = cache_clear  # type: ignore[attr-defined]
    return wrapper


# ----- async builder -----


def _build_async(
    func: Callable[..., Awaitable[Any]],
    cache: Any,
    ttl: float | None,
    soft_ttl: float | None,
    stale: StalePolicy,
    key_fn: Callable[..., Hashable],
) -> Any:
    counters = _Counters()
    has_envelope = hasattr(cache, "get_envelope")
    sf: AsyncSingleFlight[Hashable, Any] | None = None
    if has_envelope and soft_ttl is not None:
        sf = AsyncSingleFlight(cache)

    @wraps(func)
    async def wrapper(*args: Any, **kwargs: Any) -> Any:
        k = key_fn(*args, **kwargs)
        if sf is not None:
            result = await sf.get_or_create_detailed(
                k,
                lambda: func(*args, **kwargs),
                ttl=ttl,
                soft_ttl=soft_ttl,
                stale=stale,
            )
            if result.was_stale and result.revalidating:
                counters.regens += 1
            counters.hits += 1
            return result.value

        _SENTINEL: Any = _MISS
        hit = await cache.get(k, default=_SENTINEL)
        if hit is not _SENTINEL:
            counters.hits += 1
            return hit
        counters.misses += 1
        value = await func(*args, **kwargs)
        await cache.set(k, value, ttl=ttl, soft_ttl=soft_ttl)
        return value

    def cache_info() -> DecoratorInfo:
        return counters.snapshot()

    def cache_clear() -> None:
        counters.reset()

    wrapper.cache_info = cache_info  # type: ignore[attr-defined]
    wrapper.cache_clear = cache_clear  # type: ignore[attr-defined]
    return wrapper


class _Miss:
    __slots__ = ()


_MISS: Any = _Miss()


