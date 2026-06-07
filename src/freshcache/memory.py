"""In-memory transport cache. Stdlib only; thread-safe.

Implements Cache + SupportsAdd + SupportsEnvelope + SupportsInfo by
structural typing (no explicit Protocol inheritance).
"""

from __future__ import annotations

import threading
import time
from collections import OrderedDict
from collections.abc import Hashable
from typing import Any, cast

from freshcache.envelope import Envelope
from freshcache.info import CacheInfo


class MemoryCache:
    """OrderedDict-backed LRU. Lazy hard-TTL expiry; soft-TTL via Envelope.

    Suitable for tests, small services, and zero-deps environments. For higher
    throughput use freshcache.adapters.cachebox.CacheboxAdapter.
    """

    def __init__(self, maxsize: int = 1024) -> None:
        if maxsize < 1:
            raise ValueError("maxsize must be >= 1")
        self._d: OrderedDict[Hashable, tuple[Any, float | None]] = OrderedDict()
        self._maxsize = maxsize
        self._lock = threading.Lock()
        self._hits = 0
        self._misses = 0

    def _expired(self, hard_at: float | None) -> bool:
        return hard_at is not None and time.time() >= hard_at

    def get(self, key: Hashable, /, *, default: Any = None) -> Any:
        with self._lock:
            entry = self._d.get(key)
            if entry is None:
                self._misses += 1
                return default
            stored, hard_at = entry
            if self._expired(hard_at):
                del self._d[key]
                self._misses += 1
                return default
            self._d.move_to_end(key)
            self._hits += 1
        if isinstance(stored, Envelope):
            return cast(Any, stored.value)  # pyright: ignore[reportUnknownMemberType]
        return stored

    def get_strict(self, key: Hashable, /) -> Any:
        with self._lock:
            entry = self._d.get(key)
            if entry is None:
                self._misses += 1
                raise KeyError(key)
            stored, hard_at = entry
            if self._expired(hard_at):
                del self._d[key]
                self._misses += 1
                raise KeyError(key)
            self._d.move_to_end(key)
            self._hits += 1
        if isinstance(stored, Envelope):
            return cast(Any, stored.value)  # pyright: ignore[reportUnknownMemberType]
        return stored

    def get_envelope(self, key: Hashable, /) -> Envelope[Any] | None:
        with self._lock:
            entry = self._d.get(key)
            if entry is None:
                self._misses += 1
                return None
            stored, hard_at = entry
            if self._expired(hard_at):
                del self._d[key]
                self._misses += 1
                return None
            self._d.move_to_end(key)
            self._hits += 1
        if isinstance(stored, Envelope):
            return stored  # pyright: ignore[reportUnknownVariableType]
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
            self._d[key] = (stored, hard_at)
            self._d.move_to_end(key)
            while len(self._d) > self._maxsize:
                self._d.popitem(last=False)

    def delete(self, key: Hashable, /) -> bool:
        with self._lock:
            return self._d.pop(key, None) is not None

    def add(
        self,
        key: Hashable,
        value: Any,
        /,
        *,
        ttl: float | None = None,
        soft_ttl: float | None = None,
    ) -> bool:
        now = time.time()
        stored: Any = Envelope(value, now, soft_ttl) if soft_ttl is not None else value
        hard_at = now + ttl if ttl is not None else None
        with self._lock:
            existing = self._d.get(key)
            if existing is not None and not self._expired(existing[1]):
                return False
            self._d[key] = (stored, hard_at)
            self._d.move_to_end(key)
            while len(self._d) > self._maxsize:
                self._d.popitem(last=False)
            return True

    def info(self) -> CacheInfo:
        with self._lock:
            return CacheInfo(
                hits=self._hits,
                misses=self._misses,
                size=len(self._d),
                maxsize=self._maxsize,
            )

    def clear(self) -> None:
        with self._lock:
            self._d.clear()
            self._hits = 0
            self._misses = 0
