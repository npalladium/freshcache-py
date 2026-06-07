"""TypedView: typed view over an untyped Cache."""

from __future__ import annotations

from collections.abc import Callable, Hashable
from typing import Any, Generic, TypeVar, overload

from freshcache.codecs import Codec, PickleCodec
from freshcache.envelope import Envelope
from freshcache.protocols import Cache, SupportsEnvelope

K = TypeVar("K", bound=Hashable)
V = TypeVar("V")
T = TypeVar("T")


class _Nil:
    __slots__ = ()


_NIL: Any = _Nil()


def default_key_encoder(key: Hashable) -> Hashable:
    """Default: pass keys through unchanged. Backends repr() them as needed."""
    return key


class TypedView(Generic[K, V]):
    """Concrete TypedCache implementation. Wraps a Cache with codec + namespace.

    Multiple TypedView instances can share one underlying Cache, isolated by
    namespace. Codec round-trips V <-> bytes; the inner Cache stores bytes.
    """

    def __init__(
        self,
        inner: Cache,
        *,
        namespace: str,
        codec: Codec | None = None,
        key_encoder: Callable[[K], Hashable] = default_key_encoder,
    ) -> None:
        self._inner = inner
        self._namespace = namespace
        self._prefix = f"{namespace}:"
        self._codec: Codec = codec if codec is not None else PickleCodec()
        self._key_encoder = key_encoder

    def _k(self, key: K) -> Hashable:
        return f"{self._prefix}{self._key_encoder(key)!r}"

    @overload
    def get(self, key: K, /) -> V | None: ...
    @overload
    def get(self, key: K, /, *, default: V) -> V: ...
    @overload
    def get(self, key: K, /, *, default: T) -> V | T: ...

    def get(self, key: K, /, *, default: Any = None) -> Any:
        raw = self._inner.get(self._k(key), default=_NIL)
        if raw is _NIL:
            return default
        if not isinstance(raw, (bytes, bytearray)):
            # Stored without codec (mixed direct/Cache use, or a bug).
            # Treat as miss for safety.
            return default
        return self._codec.loads(bytes(raw))

    def get_strict(self, key: K, /) -> V:
        raw = self._inner.get_strict(self._k(key))
        if not isinstance(raw, (bytes, bytearray)):
            raise KeyError(key)
        loaded: V = self._codec.loads(bytes(raw))
        return loaded

    def set(
        self,
        key: K,
        value: V,
        /,
        *,
        ttl: float | None = None,
        soft_ttl: float | None = None,
    ) -> None:
        self._inner.set(
            self._k(key),
            self._codec.dumps(value),
            ttl=ttl,
            soft_ttl=soft_ttl,
        )

    def delete(self, key: K, /) -> bool:
        return self._inner.delete(self._k(key))

    def get_envelope(self, key: K, /) -> Envelope[V] | None:
        """Delegates to inner.get_envelope and decodes the payload.

        Present only if inner is SupportsEnvelope; raises AttributeError
        otherwise (caller should isinstance-check first).
        """
        if not isinstance(self._inner, SupportsEnvelope):
            raise AttributeError(
                "inner Cache does not support envelopes (SupportsEnvelope)"
            )
        raw_env = self._inner.get_envelope(self._k(key))
        if raw_env is None:
            return None
        raw_value: Any = raw_env.value  # pyright: ignore[reportUnknownMemberType]
        if isinstance(raw_value, (bytes, bytearray)):
            decoded: V = self._codec.loads(bytes(raw_value))
        else:
            decoded = raw_value
        return Envelope(decoded, raw_env.created_at, raw_env.soft_ttl)
