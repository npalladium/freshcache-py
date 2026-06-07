"""Per-key single-flight with stale-while-revalidate policies (async).

Mirrors SingleFlight using asyncio primitives. contextvars propagate across
asyncio.create_task by default (asymmetric with sync REVALIDATE — see
DESIGN.md, Consistency model).
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections import OrderedDict
from collections.abc import Awaitable, Callable, Hashable
from typing import Any, Generic, TypeVar, cast

from freshcache.protocols import AsyncCache, AsyncSupportsEnvelope
from freshcache.singleflight import Result, StalePolicy

K = TypeVar("K", bound=Hashable)
V = TypeVar("V")

_log = logging.getLogger("freshcache.async_singleflight")


def _default_error_hook(exc: BaseException) -> None:
    _log.exception("async revalidation failed", exc_info=exc)


class AsyncSingleFlight(Generic[K, V]):
    """Async per-key single-flight wrapper.
    Inner must implement AsyncCache + AsyncSupportsEnvelope."""

    def __init__(
        self,
        inner: AsyncCache,
        *,
        revalidate_backoff: float = 5.0,
        backoff_maxsize: int = 1024,
        on_revalidate_error: Callable[[BaseException], None] = _default_error_hook,
    ) -> None:
        if not isinstance(inner, AsyncSupportsEnvelope):
            raise TypeError(
                "AsyncSingleFlight requires the inner AsyncCache to implement "
                "AsyncSupportsEnvelope"
            )
        self._inner = inner
        self._envelope_inner: AsyncSupportsEnvelope = inner
        self._inflight: dict[Hashable, asyncio.Future[Any]] = {}
        self._lock = asyncio.Lock()
        self._revalidate_backoff = revalidate_backoff
        self._backoff: OrderedDict[Hashable, float] = OrderedDict()
        self._backoff_maxsize = backoff_maxsize
        self._regenerating: set[Hashable] = set()
        self._tasks: set[asyncio.Task[None]] = set()
        self._on_error = on_revalidate_error

    async def get_or_create(
        self,
        key: K,
        factory: Callable[[], Awaitable[V]],
        *,
        ttl: float | None = None,
        soft_ttl: float | None = None,
        stale: StalePolicy = StalePolicy.BLOCK,
    ) -> V:
        result = await self.get_or_create_detailed(
            key, factory, ttl=ttl, soft_ttl=soft_ttl, stale=stale
        )
        return result.value

    async def get_or_create_detailed(
        self,
        key: K,
        factory: Callable[[], Awaitable[V]],
        *,
        ttl: float | None = None,
        soft_ttl: float | None = None,
        stale: StalePolicy = StalePolicy.BLOCK,
    ) -> Result[V]:
        env = await self._envelope_inner.get_envelope(key)

        if env is not None and not env.is_stale():
            return Result(cast(V, env.value), was_stale=False, revalidating=False)

        if env is not None:
            if stale is StalePolicy.SERVE:
                return Result(cast(V, env.value), was_stale=True, revalidating=False)
            if stale is StalePolicy.REVALIDATE:
                scheduled = await self._maybe_schedule_revalidation(
                    key, factory, ttl=ttl, soft_ttl=soft_ttl
                )
                return Result(
                    cast(V, env.value), was_stale=True, revalidating=scheduled
                )

        value = await self._block_and_create(key, factory, ttl=ttl, soft_ttl=soft_ttl)
        return Result(value, was_stale=env is not None, revalidating=False)

    async def aclose(self) -> None:
        tasks = list(self._tasks)
        for t in tasks:
            t.cancel()
        for t in tasks:
            try:
                await t
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
        self._tasks.clear()

    async def __aenter__(self) -> AsyncSingleFlight[K, V]:
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.aclose()

    async def _block_and_create(
        self,
        key: K,
        factory: Callable[[], Awaitable[V]],
        *,
        ttl: float | None,
        soft_ttl: float | None,
    ) -> V:
        async with self._lock:
            fut = self._inflight.get(key)
            if fut is not None:
                wait = True
            else:
                loop = asyncio.get_running_loop()
                fut = loop.create_future()
                self._inflight[key] = fut
                wait = False

        if wait:
            return cast(V, await fut)

        try:
            value = await factory()
        except BaseException as e:
            fut.set_exception(e)
            async with self._lock:
                self._inflight.pop(key, None)
            raise
        try:
            await self._inner.set(key, value, ttl=ttl, soft_ttl=soft_ttl)
        except Exception as e:  # noqa: BLE001
            _log.warning("async cache.set failed for key=%r: %r", key, e)
        fut.set_result(value)
        async with self._lock:
            self._inflight.pop(key, None)
        return value

    async def _maybe_schedule_revalidation(
        self,
        key: K,
        factory: Callable[[], Awaitable[V]],
        *,
        ttl: float | None,
        soft_ttl: float | None,
    ) -> bool:
        now = time.monotonic()
        async with self._lock:
            backoff_until = self._backoff.get(key)
            if backoff_until is not None:
                if now < backoff_until:
                    return False
                del self._backoff[key]
            if key in self._regenerating:
                return False
            self._regenerating.add(key)

        task = asyncio.create_task(self._revalidate(key, factory, ttl, soft_ttl))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return True

    async def _revalidate(
        self,
        key: K,
        factory: Callable[[], Awaitable[V]],
        ttl: float | None,
        soft_ttl: float | None,
    ) -> None:
        try:
            value = await factory()
            await self._inner.set(key, value, ttl=ttl, soft_ttl=soft_ttl)
        except BaseException as e:
            await self._record_backoff(key)
            try:
                self._on_error(e)
            except Exception:  # noqa: BLE001
                _log.exception("on_revalidate_error hook raised")
        finally:
            async with self._lock:
                self._regenerating.discard(key)

    async def _record_backoff(self, key: Hashable) -> None:
        until = time.monotonic() + self._revalidate_backoff
        async with self._lock:
            self._backoff[key] = until
            self._backoff.move_to_end(key)
            while len(self._backoff) > self._backoff_maxsize:
                self._backoff.popitem(last=False)
