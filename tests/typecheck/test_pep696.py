"""PEP 696 TypeVar defaults — native in Python 3.13+."""

from __future__ import annotations

from collections.abc import Hashable
from typing import Any, Protocol, TypeVar, overload, runtime_checkable

K = TypeVar("K", bound=Hashable, contravariant=True, default=Hashable)
V = TypeVar("V", default=Any)
T = TypeVar("T")


@runtime_checkable
class TypedCacheD(Protocol[K, V]):
    """TypedCache with K and V defaults so users can write TypedCacheD without [K, V]."""

    @overload
    def get(self, key: K, /) -> V | None: ...
    @overload
    def get(self, key: K, /, *, default: V) -> V: ...
    @overload
    def get(self, key: K, /, *, default: T) -> V | T: ...

    def get_strict(self, key: K, /) -> V: ...
    def set(self, key: K, value: V, /) -> None: ...


class _ConcreteCache:
    def get(self, key: Any, /, *, default: Any = None) -> Any:  # noqa: ARG002
        return default

    def get_strict(self, key: Any, /) -> Any:  # noqa: ARG002
        raise KeyError

    def set(self, key: Any, value: Any, /) -> None:  # noqa: ARG002
        pass


# Without parameters: K=Hashable, V=Any
cache_default: TypedCacheD = _ConcreteCache()       # no [K, V]
v1 = cache_default.get("anything")                  # Any | None


# With explicit parameters
cache_typed: TypedCacheD[str, int] = _ConcreteCache()
v2 = cache_typed.get("k")                           # int | None
