"""Backend-side counters."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class CacheInfo:
    """Backend-side hit/miss counters. Returned by SupportsInfo.info()."""

    hits: int
    misses: int
    size: int
    maxsize: int | None
