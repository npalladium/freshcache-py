"""CachetoolsAdapter: adapt cachetools.Cache subclasses to our Cache protocol.

cachetools doesn't have per-entry TTL (TTLCache uses one global TTL). We
layer per-entry hard expiry on top by storing (value, hard_at) tuples and
checking lazily on read. The wrapped cachetools instance handles
eviction (LRU/LFU/TTL/FIFO).
"""

from __future__ import annotations

import threading
import time
from collections.abc import Hashable
from typing import Any, cast

try:
    import cachetools
except ImportError as e:  # pragma: no cover
    raise ImportError(
        "CachetoolsAdapter requires the 'cachetools' extra: "
        "pip install freshcache[cachetools]"
    ) from e

from freshcache.envelope import Envelope


class CachetoolsAdapter:
    """Thread-safe adapter over any cachetools.Cache."""

    def __init__(
        self,
        inner: cachetools.Cache[Hashable, Any] | None = None,
        *,
        maxsize: int = 1024,
    ) -> None:
        self._c: cachetools.Cache[Hashable, Any] = (
            inner if inner is not None else cachetools.LRUCache(maxsize=maxsize)
        )
        self._lock = threading.Lock()

    def _expired(self, hard_at: float | None) -> bool:
        return hard_at is not None and time.time() >= hard_at

    def _read(self, key: Hashable) -> tuple[Any, bool] | None:
        with self._lock:
            try:
                stored, hard_at = self._c[key]
            except KeyError:
                return None
            if self._expired(hard_at):
                try:
                    del self._c[key]
                except KeyError:
                    pass
                return None
            return stored, isinstance(stored, Envelope)

    def get(self, key: Hashable, /, *, default: Any = None) -> Any:
        result = self._read(key)
        if result is None:
            return default
        stored, was_env = result
        if was_env:
            return stored.value  # pyright: ignore[reportUnknownMemberType]
        return stored

    def get_strict(self, key: Hashable, /) -> Any:
        result = self._read(key)
        if result is None:
            raise KeyError(key)
        stored, was_env = result
        if was_env:
            return stored.value  # pyright: ignore[reportUnknownMemberType]
        return stored

    def get_envelope(self, key: Hashable, /) -> Envelope[Any] | None:
        result = self._read(key)
        if result is None:
            return None
        stored, was_env = result
        if was_env:
            return cast(Envelope[Any], stored)
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
        now = time.time()
        stored: Any = Envelope(value, now, soft_ttl) if soft_ttl is not None else value
        hard_at = now + ttl if ttl is not None else None
        with self._lock:
            self._c[key] = (stored, hard_at)

    def add(
        self,
        key: Hashable,
        value: Any,
        /,
        *,
        ttl: float | None = None,
        soft_ttl: float | None = None,
    ) -> bool:
        with self._lock:
            try:
                _stored, hard_at = self._c[key]
                if not self._expired(hard_at):
                    return False
            except KeyError:
                pass
        self.set(key, value, ttl=ttl, soft_ttl=soft_ttl)
        return True

    def delete(self, key: Hashable, /) -> bool:
        with self._lock:
            try:
                del self._c[key]
                return True
            except KeyError:
                return False
