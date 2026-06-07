"""NullCache: zero-storage transport that always misses.

Useful for tests and for disabling caching without changing call sites.
"""

from __future__ import annotations

from collections.abc import Hashable
from typing import Any

from freshcache.envelope import Envelope
from freshcache.info import CacheInfo


class NullCache:
    """Always misses. Writes are no-ops. Counts hits=0, misses on every get."""

    def __init__(self) -> None:
        self._misses = 0

    def get(self, key: Hashable, /, *, default: Any = None) -> Any:  # noqa: ARG002 - protocol surface
        self._misses += 1
        return default

    def get_strict(self, key: Hashable, /) -> Any:
        self._misses += 1
        raise KeyError(key)

    def get_envelope(self, key: Hashable, /) -> Envelope[Any] | None:  # noqa: ARG002
        self._misses += 1
        return None

    def set(
        self,
        key: Hashable,  # noqa: ARG002
        value: Any,  # noqa: ARG002
        /,
        *,
        ttl: float | None = None,  # noqa: ARG002
        soft_ttl: float | None = None,  # noqa: ARG002
    ) -> None:
        return None

    def delete(self, key: Hashable, /) -> bool:  # noqa: ARG002
        return False

    def add(
        self,
        key: Hashable,  # noqa: ARG002
        value: Any,  # noqa: ARG002
        /,
        *,
        ttl: float | None = None,  # noqa: ARG002
        soft_ttl: float | None = None,  # noqa: ARG002
    ) -> bool:
        # NullCache is "empty"; add succeeds in spirit (no conflict) but
        # stores nothing. Returning True keeps caller logic simple.
        return True

    def info(self) -> CacheInfo:
        return CacheInfo(hits=0, misses=self._misses, size=0, maxsize=0)
