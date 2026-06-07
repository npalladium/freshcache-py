"""AsyncRedisAdapter: transport-only AsyncCache backed by redis.asyncio."""

from __future__ import annotations

from collections.abc import Callable, Hashable
from typing import Any

try:
    from redis.asyncio import Redis as AsyncRedis
except ImportError as e:  # pragma: no cover
    raise ImportError(
        "AsyncRedisAdapter requires the 'redis' extra: pip install freshcache[redis]"
    ) from e

from freshcache.adapters.redis import (
    as_bytes,
    default_key_suffix,
    wire_unwrap,
    wire_wrap,
)
from freshcache.envelope import Envelope


class AsyncRedisAdapter:
    """Async mirror of RedisAdapter. Same wire format, same key encoding."""

    def __init__(
        self,
        client: AsyncRedis,
        *,
        namespace: str = "fc",
        key_serializer: Callable[[Hashable], str] | None = None,
    ) -> None:
        self._c = client
        self._ns = namespace
        self._key_serializer = (
            key_serializer if key_serializer is not None else default_key_suffix
        )

    def _k(self, key: Hashable) -> str:
        return f"{self._ns}:{self._key_serializer(key)}"

    async def get(self, key: Hashable, /, *, default: Any = None) -> Any:
        raw = await self._c.get(self._k(key))
        if raw is None:
            return default
        payload, _, _ = wire_unwrap(as_bytes(raw))
        return payload

    async def get_strict(self, key: Hashable, /) -> Any:
        raw = await self._c.get(self._k(key))
        if raw is None:
            raise KeyError(key)
        payload, _, _ = wire_unwrap(as_bytes(raw))
        return payload

    async def get_envelope(self, key: Hashable, /) -> Envelope[Any] | None:
        raw = await self._c.get(self._k(key))
        if raw is None:
            return None
        payload, created, soft_ttl = wire_unwrap(as_bytes(raw))
        if created is None or soft_ttl is None:
            return None
        return Envelope(payload, created, soft_ttl)

    async def set(
        self,
        key: Hashable,
        value: Any,
        /,
        *,
        ttl: float | None = None,
        soft_ttl: float | None = None,
    ) -> None:
        if not isinstance(value, (bytes, bytearray)):
            raise TypeError(
                "AsyncRedisAdapter stores bytes only. Wrap with AsyncTypedView."
            )
        blob = wire_wrap(bytes(value), soft_ttl)
        kwargs: dict[str, Any] = {}
        if ttl is not None and ttl > 0:
            kwargs["px"] = max(1, int(ttl * 1000))
        await self._c.set(self._k(key), blob, **kwargs)

    async def add(
        self,
        key: Hashable,
        value: Any,
        /,
        *,
        ttl: float | None = None,
        soft_ttl: float | None = None,
    ) -> bool:
        if not isinstance(value, (bytes, bytearray)):
            raise TypeError("AsyncRedisAdapter stores bytes only.")
        blob = wire_wrap(bytes(value), soft_ttl)
        kwargs: dict[str, Any] = {"nx": True}
        if ttl is not None and ttl > 0:
            kwargs["px"] = max(1, int(ttl * 1000))
        result = await self._c.set(self._k(key), blob, **kwargs)
        return bool(result)

    async def delete(self, key: Hashable, /) -> bool:
        n = await self._c.delete(self._k(key))
        try:
            return int(n) > 0
        except (TypeError, ValueError):
            return bool(n)
