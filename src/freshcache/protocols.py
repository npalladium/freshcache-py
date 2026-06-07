"""Core protocols for freshcache.

Two-layer design:
- Cache / AsyncCache: transport (Hashable keys, Any values, no generics).
- TypedCache[K, V] / AsyncTypedCache[K, V]: typed view (codec-backed).

Capability markers (SupportsAdd, SupportsEnvelope, SupportsInfo) are
structural Protocols that backends may implement.
"""

from __future__ import annotations

from collections.abc import Hashable
from typing import Any, Protocol, TypeVar, overload, runtime_checkable

from freshcache.envelope import Envelope
from freshcache.info import CacheInfo

K = TypeVar("K", bound=Hashable, contravariant=True)
V = TypeVar("V")
T = TypeVar("T")


# ----- Transport layer (untyped) -----


@runtime_checkable
class Cache(Protocol):
    """Transport-layer contract. Backends implement this.
    Hashable keys, Any values; no generic parameters."""

    def get(self, key: Hashable, /, *, default: Any = None) -> Any: ...
    def get_strict(self, key: Hashable, /) -> Any: ...
    def set(
        self,
        key: Hashable,
        value: Any,
        /,
        *,
        ttl: float | None = None,
        soft_ttl: float | None = None,
    ) -> None: ...
    def delete(self, key: Hashable, /) -> bool: ...


@runtime_checkable
class SupportsAdd(Protocol):
    """First-writer-wins. Used by future DistributedSingleFlight."""

    def add(
        self,
        key: Hashable,
        value: Any,
        /,
        *,
        ttl: float | None = None,
        soft_ttl: float | None = None,
    ) -> bool: ...


@runtime_checkable
class SupportsEnvelope(Protocol):
    """Exposes timestamped envelope for staleness inspection.
    Required by SingleFlight stale policies."""

    def get_envelope(self, key: Hashable, /) -> Envelope[Any] | None: ...


@runtime_checkable
class SupportsInfo(Protocol):
    """Backend-side hit/miss counters."""

    def info(self) -> CacheInfo: ...


# ----- Typed view layer -----


@runtime_checkable
class TypedCache(Protocol[K, V]):
    """Typed view. Parameterized; tied to specific K, V.
    Usually obtained by wrapping a Cache with TypedView."""

    @overload
    def get(self, key: K, /) -> V | None: ...
    @overload
    def get(self, key: K, /, *, default: V) -> V: ...
    @overload
    def get(self, key: K, /, *, default: T) -> V | T: ...

    def get_strict(self, key: K, /) -> V: ...
    def set(
        self,
        key: K,
        value: V,
        /,
        *,
        ttl: float | None = None,
        soft_ttl: float | None = None,
    ) -> None: ...
    def delete(self, key: K, /) -> bool: ...


_V_in = TypeVar("_V_in", contravariant=True)


@runtime_checkable
class TypedSupportsAdd(Protocol[K, _V_in]):
    def add(
        self,
        key: K,
        value: _V_in,
        /,
        *,
        ttl: float | None = None,
        soft_ttl: float | None = None,
    ) -> bool: ...


@runtime_checkable
class TypedSupportsEnvelope(Protocol[K, V]):
    # V is invariant here because Envelope[V] is invariant in V (frozen
    # dataclass with V as a stored field). Variance is documentary; not
    # structurally enforced at the typed-mirror boundary.
    def get_envelope(self, key: K, /) -> Envelope[V] | None: ...


# ----- Async mirrors -----


@runtime_checkable
class AsyncCache(Protocol):
    """Async transport-layer contract."""

    async def get(self, key: Hashable, /, *, default: Any = None) -> Any: ...
    async def get_strict(self, key: Hashable, /) -> Any: ...
    async def set(
        self,
        key: Hashable,
        value: Any,
        /,
        *,
        ttl: float | None = None,
        soft_ttl: float | None = None,
    ) -> None: ...
    async def delete(self, key: Hashable, /) -> bool: ...


@runtime_checkable
class AsyncSupportsAdd(Protocol):
    async def add(
        self,
        key: Hashable,
        value: Any,
        /,
        *,
        ttl: float | None = None,
        soft_ttl: float | None = None,
    ) -> bool: ...


@runtime_checkable
class AsyncSupportsEnvelope(Protocol):
    async def get_envelope(self, key: Hashable, /) -> Envelope[Any] | None: ...


@runtime_checkable
class AsyncTypedCache(Protocol[K, V]):
    """Async typed view."""

    @overload
    async def get(self, key: K, /) -> V | None: ...
    @overload
    async def get(self, key: K, /, *, default: V) -> V: ...
    @overload
    async def get(self, key: K, /, *, default: T) -> V | T: ...

    async def get_strict(self, key: K, /) -> V: ...
    async def set(
        self,
        key: K,
        value: V,
        /,
        *,
        ttl: float | None = None,
        soft_ttl: float | None = None,
    ) -> None: ...
    async def delete(self, key: K, /) -> bool: ...
