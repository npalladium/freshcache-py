"""CacheboxAdapter: Rust-backed in-process Cache.

Uses cachebox.VTTLCache for per-entry TTL + LRU semantics. Envelope-wraps
when soft_ttl is set.
"""

from __future__ import annotations

import threading
from collections.abc import Hashable
from typing import Any, cast

try:
    import cachebox  # type: ignore[import-untyped]
except ImportError as e:  # pragma: no cover
    raise ImportError(
        "CacheboxAdapter requires the 'cachebox' extra: "
        "pip install freshcache[cachebox]"
    ) from e

from freshcache.envelope import Envelope


class CacheboxAdapter:
    """Cache backed by cachebox.VTTLCache. Thread-safe (cachebox is)."""

    def __init__(
        self,
        maxsize: int = 1024,
        *,
        inner: cachebox.VTTLCache[Hashable, Any] | None = None,
    ) -> None:
        self._c: cachebox.VTTLCache[Hashable, Any] = (
            inner if inner is not None else cachebox.VTTLCache(maxsize)
        )
        # Local stat counters; cachebox tracks size itself.
        self._hits = 0
        self._misses = 0
        self._stat_lock = threading.Lock()

    def get(self, key: Hashable, /, *, default: Any = None) -> Any:
        sentinel: Any = _SENTINEL
        value = self._c.get(key, sentinel)
        if value is sentinel:
            with self._stat_lock:
                self._misses += 1
            return default
        with self._stat_lock:
            self._hits += 1
        if isinstance(value, Envelope):
            return cast(Any, value.value)  # pyright: ignore[reportUnknownMemberType]
        return value

    def get_strict(self, key: Hashable, /) -> Any:
        sentinel: Any = _SENTINEL
        value = self._c.get(key, sentinel)
        if value is sentinel:
            with self._stat_lock:
                self._misses += 1
            raise KeyError(key)
        with self._stat_lock:
            self._hits += 1
        if isinstance(value, Envelope):
            return cast(Any, value.value)  # pyright: ignore[reportUnknownMemberType]
        return value

    def get_envelope(self, key: Hashable, /) -> Envelope[Any] | None:
        sentinel: Any = _SENTINEL
        value = self._c.get(key, sentinel)
        if value is sentinel:
            with self._stat_lock:
                self._misses += 1
            return None
        with self._stat_lock:
            self._hits += 1
        if isinstance(value, Envelope):
            return value  # pyright: ignore[reportUnknownVariableType]
        return None

    def set(
        self,
        key: Hashable,
        value: Any,
        /,
        *,
        ttl: float | None = None,
        soft_ttl: float | None = None,
    ) -> None:
        import time

        stored: Any = (
            Envelope(value, time.time(), soft_ttl) if soft_ttl is not None else value
        )
        self._c.insert(key, stored, ttl=ttl)

    def add(
        self,
        key: Hashable,
        value: Any,
        /,
        *,
        ttl: float | None = None,
        soft_ttl: float | None = None,
    ) -> bool:
        if self._c.contains(key):
            return False
        self.set(key, value, ttl=ttl, soft_ttl=soft_ttl)
        return True

    def delete(self, key: Hashable, /) -> bool:
        try:
            self._c.pop(key)
            return True
        except KeyError:
            return False


class _Sentinel:
    __slots__ = ()


_SENTINEL: Any = _Sentinel()
