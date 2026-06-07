# freshcache — consistency model

This file lists the guarantees freshcache makes (and explicitly does NOT make).
Use it when deciding whether a given backend / policy combination is safe for
your use case.

## Per-operation guarantees

| Operation | Guarantee |
|-----------|-----------|
| `get` after `set` (same process, same cache) | Read-your-writes. |
| `get` after `set` (different process, shared backend) | Eventually consistent — typically <10ms on Redis on a healthy network. |
| `set` after `set` (concurrent, same key) | Last-writer-wins. Use `add()` (where supported) for first-writer-wins. |
| `delete` | Idempotent. Returns existence at the moment of the call. |
| **Hard TTL (`ttl=`)** | Best-effort. Redis enforces server-side; in-memory backends enforce lazily on read. May expire early under memory pressure / eviction. |
| **Soft TTL (`soft_ttl=`)** | Wall-clock-based (`time.time()`). Cross-process boundary is subject to **NTP drift** (often seconds). Acceptable for stampede mitigation; **not for security-relevant freshness**. |
| `SingleFlight.get_or_create` on miss | At most one concurrent `factory()` per key per process. Other callers block on the same per-key event. |
| `SingleFlight.get_or_create` with `BLOCK` on stale | Same as miss (block until regenerated). |
| `SingleFlight.get_or_create` with `SERVE` on stale | Return stale immediately. No regeneration. |
| `SingleFlight.get_or_create` with `REVALIDATE` on stale | Return stale immediately. At most one background regen per key per process. Cross-process: N processes may each spawn one. |
| Revalidation failure | Stale value remains served until hard TTL expires. `on_revalidate_error` hook invoked. Backoff prevents retry storms. |
| Backend failure on `get` | Implementation-defined. Default for built-ins: re-raise. Caller-side `@cached` falls through to factory. |
| Backend failure on `set` | Logged; not propagated. SingleFlight delivers the value to in-process waiters even if `set` fails. |
| Codec error on read (`loads()` raises `CodecError`) | `get` returns `default` (treats as miss). `get_strict` raises. `@cached` recomputes via factory. |

## Context propagation across REVALIDATE

| Mode | `contextvars` | `threading.local` |
|------|---------------|-------------------|
| `SingleFlight` (sync) | NOT propagated — factory runs in a `ThreadPoolExecutor` worker. | NOT propagated. |
| `AsyncSingleFlight` | Propagated — factory runs via `asyncio.create_task`, which copies the current context. | N/A. |

This asymmetry is intentional and documented. If you need request context in
sync REVALIDATE, pass a context-aware executor to `SingleFlight(executor=…)`.

## What is NOT guaranteed

- Cache coherence across **different** backends.
- Transactional multi-key operations.
- Invalidation propagation between independent caches.
- Exactly-once factory execution **across the fleet** under `REVALIDATE`
  (single-flight is per-process; a `DistributedSingleFlight` based on Redis
  SET NX + fencing tokens is planned for v0.2).
- Strict ordering of `set` operations across processes (Redis serializes per
  connection, not across connections).

## Choosing TTLs

- `ttl` (hard): bounds how long a value can be served. After it expires, the
  next `get` returns a miss; the backend may delete the entry.
- `soft_ttl` (soft): used by `SingleFlight` to decide *staleness*. The value
  remains served until `ttl` expires. Combine with `stale=REVALIDATE` for
  stampede protection without latency spikes.

Typical pattern:

```python
@cached(cache=users, ttl=3600, soft_ttl=600, stale=StalePolicy.REVALIDATE)
def get_user(uid: str) -> User:
    return User.fetch(uid)
```

- Fresh window: 0–600 s — return cached value, no work.
- Stale window: 600–3600 s — return cached value immediately, schedule async
  refresh.
- Expired: ≥3600 s — block and recompute.
