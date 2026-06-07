"""Per-key single-flight with stale-while-revalidate policies (sync)."""

from __future__ import annotations

import logging
import threading
import time
from collections import OrderedDict
from collections.abc import Callable, Hashable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from enum import Enum
from typing import Any, Generic, TypeVar, cast

from freshcache.protocols import Cache, SupportsEnvelope

K = TypeVar("K", bound=Hashable)
V = TypeVar("V")

_log = logging.getLogger("freshcache.singleflight")


class StalePolicy(Enum):
    BLOCK = "block"  # treat stale as miss; block until regenerated
    SERVE = "serve"  # return stale; never regenerate
    REVALIDATE = "revalidate"  # return stale immediately; regenerate in background


@dataclass(frozen=True, slots=True)
class Result(Generic[V]):
    value: V
    was_stale: bool
    revalidating: bool


def _default_error_hook(exc: BaseException) -> None:
    _log.exception("revalidation failed", exc_info=exc)


class SingleFlight(Generic[K, V]):
    """Per-key single-flight wrapper. Inner must implement Cache + SupportsEnvelope."""

    def __init__(
        self,
        inner: Cache,
        *,
        executor: ThreadPoolExecutor | None = None,
        max_workers: int = 4,
        revalidate_backoff: float = 5.0,
        backoff_maxsize: int = 1024,
        on_revalidate_error: Callable[[BaseException], None] = _default_error_hook,
    ) -> None:
        if not isinstance(inner, SupportsEnvelope):
            raise TypeError(
                "SingleFlight requires the inner Cache to implement SupportsEnvelope"
            )
        self._inner = inner
        self._envelope_inner: SupportsEnvelope = inner
        self._inflight: dict[Hashable, threading.Event] = {}
        self._results: dict[Hashable, Any] = {}
        self._errors: dict[Hashable, BaseException] = {}
        self._lock = threading.Lock()

        self._executor = executor
        self._owned_executor = executor is None
        self._max_workers = max_workers
        self._revalidate_backoff = revalidate_backoff
        self._backoff: OrderedDict[Hashable, float] = OrderedDict()
        self._backoff_maxsize = backoff_maxsize
        self._regenerating: set[Hashable] = set()
        self._on_error = on_revalidate_error

    # ----- public API -----

    def get_or_create(
        self,
        key: K,
        factory: Callable[[], V],
        *,
        ttl: float | None = None,
        soft_ttl: float | None = None,
        stale: StalePolicy = StalePolicy.BLOCK,
    ) -> V:
        return self.get_or_create_detailed(
            key, factory, ttl=ttl, soft_ttl=soft_ttl, stale=stale
        ).value

    def get_or_create_detailed(
        self,
        key: K,
        factory: Callable[[], V],
        *,
        ttl: float | None = None,
        soft_ttl: float | None = None,
        stale: StalePolicy = StalePolicy.BLOCK,
    ) -> Result[V]:
        env = self._envelope_inner.get_envelope(key)

        if env is not None and not env.is_stale():
            return Result(cast(V, env.value), was_stale=False, revalidating=False)

        if env is not None:  # stale
            if stale is StalePolicy.SERVE:
                return Result(cast(V, env.value), was_stale=True, revalidating=False)
            if stale is StalePolicy.REVALIDATE:
                scheduled = self._maybe_schedule_revalidation(
                    key, factory, ttl=ttl, soft_ttl=soft_ttl
                )
                return Result(
                    cast(V, env.value), was_stale=True, revalidating=scheduled
                )

        # BLOCK on stale, or full miss.
        value = self._block_and_create(key, factory, ttl=ttl, soft_ttl=soft_ttl)
        return Result(value, was_stale=env is not None, revalidating=False)

    def close(self) -> None:
        with self._lock:
            if self._executor is not None and self._owned_executor:
                self._executor.shutdown(wait=False)
                self._executor = None

    def __enter__(self) -> SingleFlight[K, V]:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # ----- internals -----

    def _block_and_create(
        self,
        key: K,
        factory: Callable[[], V],
        *,
        ttl: float | None,
        soft_ttl: float | None,
    ) -> V:
        with self._lock:
            event = self._inflight.get(key)
            if event is not None:
                wait = True
            else:
                event = threading.Event()
                self._inflight[key] = event
                wait = False

        if wait:
            event.wait()
            with self._lock:
                err = self._errors.pop(key, None)
                if err is not None:
                    raise err
                if key in self._results:
                    return cast(V, self._results.pop(key))
            # Producer cleaned up before we read — re-enter.
            return self.get_or_create(key, factory, ttl=ttl, soft_ttl=soft_ttl)

        try:
            value = factory()
        except BaseException as e:
            with self._lock:
                self._errors[key] = e
                event.set()
                self._inflight.pop(key, None)
            raise
        try:
            self._inner.set(key, value, ttl=ttl, soft_ttl=soft_ttl)
        except Exception as e:  # noqa: BLE001 - log and continue
            _log.warning("cache.set failed for key=%r: %r", key, e)
        with self._lock:
            self._results[key] = value
            event.set()
            self._inflight.pop(key, None)
        return value

    def _maybe_schedule_revalidation(
        self,
        key: K,
        factory: Callable[[], V],
        *,
        ttl: float | None,
        soft_ttl: float | None,
    ) -> bool:
        now = time.monotonic()
        with self._lock:
            backoff_until = self._backoff.get(key)
            if backoff_until is not None:
                if now < backoff_until:
                    return False
                del self._backoff[key]
            if key in self._regenerating:
                return False
            self._regenerating.add(key)
            executor = self._ensure_executor_locked()

        executor.submit(self._revalidate, key, factory, ttl, soft_ttl)
        return True

    def _ensure_executor_locked(self) -> ThreadPoolExecutor:
        if self._executor is None:
            self._executor = ThreadPoolExecutor(
                max_workers=self._max_workers,
                thread_name_prefix="freshcache-sf",
            )
            self._owned_executor = True
        return self._executor

    def _revalidate(
        self,
        key: K,
        factory: Callable[[], V],
        ttl: float | None,
        soft_ttl: float | None,
    ) -> None:
        try:
            value = factory()
            self._inner.set(key, value, ttl=ttl, soft_ttl=soft_ttl)
        except BaseException as e:
            self._record_backoff(key)
            try:
                self._on_error(e)
            except Exception:  # noqa: BLE001 - hook is user code
                _log.exception("on_revalidate_error hook raised")
        finally:
            with self._lock:
                self._regenerating.discard(key)

    def _record_backoff(self, key: Hashable) -> None:
        until = time.monotonic() + self._revalidate_backoff
        with self._lock:
            self._backoff[key] = until
            self._backoff.move_to_end(key)
            while len(self._backoff) > self._backoff_maxsize:
                self._backoff.popitem(last=False)
