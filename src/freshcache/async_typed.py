"""AsyncTypedView: typed view over an AsyncCache."""

from __future__ import annotations

from collections.abc import Callable, Hashable
from typing import Any, Generic, TypeVar, overload

from freshcache.codecs import Codec, PickleCodec
from freshcache.envelope import Envelope
from freshcache.protocols import AsyncCache, AsyncSupportsEnvelope
from freshcache.typed import default_key_encoder

K = TypeVar("K", bound=Hashable)
V = TypeVar("V")
T = TypeVar("T")


class _Nil:
    __slots__ = ()


_NIL: Any = _Nil()


class AsyncTypedView(Generic[K, V]):
    """Concrete AsyncTypedCache implementation. Same shape as TypedView."""

    def __init__(
        self,
        inner: AsyncCache,
        *,
        namespace: str,
        codec: Codec | None = None,
        key_encoder: Callable[[K], Hashable] = default_key_encoder,
    ) -> None:
        self._inner = inner
        self._namespace = namespace
        self._codec: Codec = codec if codec is not None else PickleCodec()
        self._key_encoder = key_encoder

    def _k(self, key: K) -> Hashable:
        return f"{self._namespace}:{self._key_encoder(key)!r}"

    @overload
    async def get(self, key: K, /) -> V | None: ...
    @overload
    async def get(self, key: K, /, *, default: V) -> V: ...
    @overload
    async def get(self, key: K, /, *, default: T) -> V | T: ...

    async def get(self, key: K, /, *, default: Any = None) -> Any:
        raw = await self._inner.get(self._k(key), default=_NIL)
        if raw is _NIL:
            return default
        if not isinstance(raw, (bytes, bytearray)):
            return default
        return self._codec.loads(bytes(raw))

    async def get_strict(self, key: K, /) -> V:
        raw = await self._inner.get_strict(self._k(key))
        if not isinstance(raw, (bytes, bytearray)):
            raise KeyError(key)
        loaded: V = self._codec.loads(bytes(raw))
        return loaded

    async def set(
        self,
        key: K,
        value: V,
        /,
        *,
        ttl: float | None = None,
        soft_ttl: float | None = None,
    ) -> None:
        await self._inner.set(
            self._k(key),
            self._codec.dumps(value),
            ttl=ttl,
            soft_ttl=soft_ttl,
        )

    async def delete(self, key: K, /) -> bool:
        return await self._inner.delete(self._k(key))

    async def get_envelope(self, key: K, /) -> Envelope[V] | None:
        if not isinstance(self._inner, AsyncSupportsEnvelope):
            raise AttributeError(
                "inner AsyncCache does not support envelopes (AsyncSupportsEnvelope)"
            )
        raw_env = await self._inner.get_envelope(self._k(key))
        if raw_env is None:
            return None
        raw_value: Any = raw_env.value  # pyright: ignore[reportUnknownMemberType]
        if isinstance(raw_value, (bytes, bytearray)):
            decoded: V = self._codec.loads(bytes(raw_value))
        else:
            decoded = raw_value
        return Envelope(decoded, raw_env.created_at, raw_env.soft_ttl)
