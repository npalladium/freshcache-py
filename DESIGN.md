# `freshcache` — design

A narrowly-scoped Python caching library: **typed Protocol layering + single-flight with stale-while-revalidate**. Bring your own backend.

## Scope

Three contributions, nothing more:

1. **A two-layer Protocol design**: `Cache` (untyped transport) + `TypedCache[K, V]` (typed view). Backends implement transport; users compose typed views on top.
2. **`SingleFlight` wrappers** with three stale-while-revalidate policies for both sync and async code.
3. **`@cached` decorator** that statically type-checks return types against a `TypedCache`.

Everything else — LRU eviction, Redis protocol, memcached, disk persistence — is delegated to existing libraries (`cachebox`, `cachetools`, `redis-py`) via thin adapters in `freshcache.adapters/`.

## Non-goals

- Not a storage engine. We don't implement LRU, LFU, TTL eviction.
- Not a Redis/Memcached client. We adapt existing ones.
- Not a cache coherence protocol. No pub/sub-based invalidation propagation.
- Not a replacement for `functools.lru_cache`. The Protocol layer adds overhead; for sub-microsecond hot paths, use the stdlib.
- Not a multi-key transactional store.

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│  @cached(stale=…)         sync + async, type-inferred       │
├─────────────────────────────────────────────────────────────┤
│  SingleFlight    BLOCK / SERVE / REVALIDATE policies        │
├─────────────────────────────────────────────────────────────┤
│  TypedCache[K, V]    typed view: codec + namespace          │
│   └─ concrete: TypedView                                    │
├─────────────────────────────────────────────────────────────┤
│  Cache (transport)  +  SupportsAdd  +  SupportsEnvelope     │
│  +  SupportsInfo                                            │
├─────────────────────────────────────────────────────────────┤
│  Adapters: cachebox / cachetools / redis / redis.asyncio    │
│  Built-in: MemoryCache (stdlib OrderedDict), NullCache      │
└─────────────────────────────────────────────────────────────┘
```

The split: backends own **transport** (bytes, TTLs, distributed semantics). `TypedView` owns **schema** (V's shape, serialization, namespacing). They compose.

## Protocols

### `Cache` — the transport contract

```python
@runtime_checkable
class Cache(Protocol):
    """Transport-layer contract. Backends implement this.
    Operates on Hashable keys and Any values. No generic parameters."""

    def get(self, key: Hashable, /, *, default: Any = None) -> Any: ...
    def get_strict(self, key: Hashable, /) -> Any: ...        # raises KeyError on miss
    def set(self, key: Hashable, value: Any, /, *,
            ttl: float | None = None,                          # hard TTL, seconds
            soft_ttl: float | None = None) -> None: ...
    def delete(self, key: Hashable, /) -> bool: ...
```

Capability markers live on `Cache` — transport-level features that some backends support and others don't:

```python
@runtime_checkable
class SupportsAdd(Protocol):
    """First-writer-wins. Required by SingleFlight cross-process mode (v0.2)."""
    def add(self, key: Hashable, value: Any, /, *,
            ttl: float | None = None,
            soft_ttl: float | None = None) -> bool: ...

@runtime_checkable
class SupportsEnvelope(Protocol):
    """Exposes the envelope (with timestamps) for staleness inspection.
    Required by SingleFlight stale policies."""
    def get_envelope(self, key: Hashable, /) -> Envelope[Any] | None: ...

@runtime_checkable
class SupportsInfo(Protocol):
    """Backend-side hit/miss counters (separate from @cached's counters)."""
    def info(self) -> CacheInfo: ...

# Async mirrors: AsyncCache, AsyncSupportsAdd, AsyncSupportsEnvelope, AsyncSupportsInfo
```

Backend authors think in this layer: "I implement `Cache`. I support `Add` and `Envelope`. I don't implement `Info`." Done.

### `TypedCache[K, V]` — the typed view

```python
K = TypeVar("K", bound=Hashable)
V = TypeVar("V")
T = TypeVar("T")

@runtime_checkable
class TypedCache(Protocol[K, V]):
    """User-facing typed view. Parameterized by K, V.
    Usually obtained by wrapping a Cache with TypedView(...)."""

    @overload
    def get(self, key: K, /) -> V | None: ...
    @overload
    def get(self, key: K, /, *, default: V) -> V: ...
    @overload
    def get(self, key: K, /, *, default: T) -> V | T: ...

    def get_strict(self, key: K, /) -> V: ...
    def set(self, key: K, value: V, /, *,
            ttl: float | None = None,
            soft_ttl: float | None = None) -> None: ...
    def delete(self, key: K, /) -> bool: ...
```

The three `get` overloads cover the useful cases:

```python
users: TypedCache[str, User]

users.get("alice")                       # -> User | None
users.get("alice", default=User.guest()) # -> User    (default same type as V)
users.get("alice", default=MISSING)      # -> User | _Missing  (caller-supplied sentinel)
users.get_strict("alice")                # -> User    (raises KeyError on miss)
```

The **caller-supplied sentinel** path makes the `MISSING` pattern opt-in via `default=` rather than forced everywhere. Callers who need to distinguish "absent" from "present-with-None" pass their own sentinel; type checker tracks it.

Capability markers have typed mirrors:

```python
class TypedSupportsAdd(Protocol[K, V]):
    def add(self, key: K, value: V, /, *,
            ttl: float | None = None,
            soft_ttl: float | None = None) -> bool: ...

class TypedSupportsEnvelope(Protocol[K, V]):
    def get_envelope(self, key: K, /) -> Envelope[V] | None: ...
```

### Why two Protocols rather than one generic `Cache[K, V]`

1. **Backend authors implement one thing.** `Cache` is concrete (Hashable, Any). No reasoning about generics, no wondering whether to expose a typed surface — that's `TypedView`'s job.
2. **Separation of concerns matches reality.** Transport (bytes, TTLs, distributed semantics) is fundamentally different from schema (V's shape, serialization, namespacing). One Protocol forces every backend to handle both.
3. **Multiple typed views over one backend.** Three `TypedCache` instances sharing one Redis connection is the common case. With a single generic Protocol, you'd need per-V instances and lose connection sharing.
4. **Backends can still natively implement `TypedCache`.** A future schema-aware backend (e.g., a protobuf-typed adapter) can implement `TypedCache[K, V]` directly without going through `TypedView`. Both paths supported.

## The bridge: `TypedView`

The concrete that wraps a `Cache` into a `TypedCache[K, V]`. This is what most users instantiate.

```python
class TypedView(Generic[K, V]):
    """Typed view over a Cache. Adds codec + namespace.
    Multiple TypedViews can share one underlying Cache, isolated by namespace."""

    def __init__(
        self,
        inner: Cache,
        *,
        namespace: str,
        codec: Codec = PickleCodec(),               # V↔bytes
        key_encoder: Callable[[K], Hashable] = default_key_encoder,
        validate: bool = False,                     # isinstance(value, type_) on set
        value_type: type[V] | None = None,          # required if validate=True
    ): ...

    def _wrap_key(self, key: K) -> str:
        return f"{self._namespace}:{self._key_encoder(key)}"

    def get(self, key, /, *, default=_NIL):
        raw = self._inner.get(self._wrap_key(key), default=_NIL)
        if raw is _NIL: return None if default is _NIL else default
        return self._codec.loads(raw)
    # set / delete / get_strict / etc. — small
```

Three typed views over one backend:

```python
redis_cache: Cache = RedisAdapter(client)   # one connection, one transport

users:   TypedCache[str, User]              = TypedView(redis_cache, namespace="users",  codec=JsonCodec(User))
scores:  TypedCache[tuple[str, int], float] = TypedView(redis_cache, namespace="scores")
configs: TypedCache[str, dict[str, Any]]    = TypedView(redis_cache, namespace="cfg")

users.set("alice", User(name="Alice", age=30))
maybe_user = users.get("alice")                    # type: User | None
user       = users.get_strict("alice")             # type: User
guest      = users.get("alice", default=User.guest())  # type: User
```

## `Codec` — pluggable serialization

```python
@runtime_checkable
class Codec(Protocol):
    """Serializer for cached values. Typically Codec[Any]; the V comes
    from the enclosing TypedView's type parameter."""
    def dumps(self, value: Any, /) -> bytes: ...
    def loads(self, blob: bytes, /) -> Any: ...
```

Built-in codecs in `freshcache.codecs`:

- **`PickleCodec()`** — default. Handles any Python object. Don't use across trust boundaries.
- **`JsonCodec(type_=None)`** — JSON. Restricts to JSON-shape data. Optional `type_=` enables Pydantic / `dataclass` deserialization if installed.
- **`MsgpackCodec()`** — compact, cross-language. Requires `freshcache[msgpack]`.
- **`GzipCodec(inner: Codec, level: int = 6)`** — wraps another codec, gzipping its output. Useful for large blobs.
- **`ZstdCodec(inner: Codec, level: int = 3)`** — same with zstd. Faster + better ratio than gzip. Requires `freshcache[zstd]`.

### Custom codec for type-aware serialization

```python
class PydanticCodec(Generic[V]):
    def __init__(self, type_: type[V]): self._t = type_
    def dumps(self, value): return value.model_dump_json().encode()
    def loads(self, blob): return self._t.model_validate_json(blob)

users = TypedView(redis_cache, namespace="users", codec=PydanticCodec(User))
```

### Composition (wrapping codecs)

Codecs compose by wrapping each other. This is the right place to layer compression, encryption, or schema-tagging:

```python
# gzip(json(User))
users = TypedView(redis_cache, namespace="users",
                  codec=GzipCodec(JsonCodec(User)))

# zstd(msgpack(...))
configs = TypedView(redis_cache, namespace="cfg",
                    codec=ZstdCodec(MsgpackCodec()))

# Your own composition layer:
class VersionedCodec:
    """Writes a version header; raises CodecError on mismatch."""
    VERSION = b"v3"
    def __init__(self, inner: Codec): self._inner = inner
    def dumps(self, v): return self.VERSION + b":" + self._inner.dumps(v)
    def loads(self, b):
        ver, _, payload = b.partition(b":")
        if ver != self.VERSION:
            raise CodecError(f"got {ver!r}, expected {self.VERSION!r}")
        return self._inner.loads(payload)
```

### Codec design notes

- **`Codec[Any]`-typed, not `Codec[V]`.** The V comes from the `TypedView`'s declared parameter. Parameterizing codecs would force a new codec instance per value type — more friction than the type-safety is worth. Codec is the deserialization boundary; `TypedView` is where V is enforced.
- **Sync only.** Serialization is CPU-bound; `await codec.dumps(v)` doesn't help. If you genuinely need async serialization (e.g., calling out to a schema registry), do it in the factory passed to `@cached` / `SingleFlight`, not in the codec. Keeps the Protocol surface single-shaped.
- **Per-call codec overrides are not supported.** A `TypedView` is a coherent V-typed namespace. If you need multiple V types in one place, use multiple `TypedView`s. Per-call overrides invite type bugs and break the layered design.
- **`CodecError`** (subclass of `ValueError`) is raised by `loads` when a blob can't be deserialized. Behavior: `get` returns `default` (treats as miss); `get_strict` raises; `@cached` recomputes via factory. See the Consistency model table.
- **Schema migration: two patterns.** Bump the `TypedView` namespace (simpler, drops the cache for that version) or write a version header in the codec (preserves more entries, more complex). Namespace bump is the default recommendation; codec versioning when cache loss is unacceptable.

## Envelope (only when soft TTL is used)

For raw caching, values are stored as-is. The envelope wrapper is introduced only when a caller passes `soft_ttl`.

```python
@dataclass(frozen=True, slots=True)
class Envelope(Generic[V]):
    value: V
    created_at: float                  # time.time() at set; wall clock
    soft_ttl: float                    # seconds; if envelope exists, soft_ttl is set
```

Behavior:

- **MemoryCache:** if `soft_ttl` set, stores `Envelope(value, now, soft_ttl)`; otherwise raw. On read, `isinstance` check unwraps.
- **RedisAdapter:** pickles an `Envelope` blob if `soft_ttl` set; otherwise pickles raw value. Read deserializes; `isinstance` check unwraps.
- **TypedView:** transparent. Soft TTL on `TypedView.set` propagates to the inner `Cache.set`. The codec serializes the user's V; the inner cache wraps in Envelope if needed.

The envelope tax (CPU + bytes) is paid only by callers who use soft TTL.

### Wall-clock caveat

`Envelope.created_at` is wall clock (`time.time()`), not monotonic, because soft TTL must be comparable across processes.

In a distributed setup, NTP drift between hosts (seconds in the wild) directly translates into soft-TTL boundary imprecision. **Use `soft_ttl` for stampede mitigation only.** For security-relevant freshness (auth tokens, short-lived locks), use hard `ttl` — Redis enforces it server-side, no clock concerns.

## `SingleFlight` with stale policies

```python
class StalePolicy(Enum):
    BLOCK      = "block"        # treat stale as miss; block until regenerated
    SERVE      = "serve"        # return stale; never regenerate
    REVALIDATE = "revalidate"   # return stale immediately; regenerate in background

@dataclass(frozen=True, slots=True)
class Result(Generic[V]):
    value: V
    was_stale: bool
    revalidating: bool

class SingleFlight(Generic[K, V]):
    @overload
    def __init__(self, inner: TypedCache[K, V], *, ...): ...
    @overload
    def __init__(self, inner: Cache, *, ...): ...   # K=Hashable, V=Any

    def get_or_create(
        self,
        key: K,
        factory: Callable[[], V],
        *,
        ttl: float | None = None,
        soft_ttl: float | None = None,
        stale: StalePolicy = StalePolicy.BLOCK,
    ) -> V: ...

    def get_or_create_detailed(self, ...) -> Result[V]: ...
```

`SingleFlight` accepts either:
- A `TypedCache[K, V]` — types flow through; `factory` must return `V`.
- A `Cache` — `K=Hashable, V=Any`; no static checking on factory.

The inner cache must implement `SupportsEnvelope` (or `TypedSupportsEnvelope` for typed views). Runtime `isinstance` check at construction with a clear error.

### State machine

| Envelope state | Policy | Behavior |
|---|---|---|
| missing | any | Block on per-key event. First arrival runs `factory`; others wait. |
| present, fresh | any | Return value immediately. |
| present, stale | `BLOCK` | Block, regenerate. |
| present, stale | `SERVE` | Return stale; no regeneration. |
| present, stale | `REVALIDATE` | Return stale immediately. If no in-flight regen and not in backoff, schedule one. |

### Key details

- **Result delivery to waiters.** The factory result is stashed alongside the per-key Event; waiters read directly (no second cache round-trip). Refcounted cleanup.
- **Backend failure during set.** If `factory()` succeeds but `cache.set()` fails, the in-process result is still delivered to waiters; failure logged via `on_revalidate_error`. Next call sees a backend miss.
- **`factory` exception** propagates to all waiters. Backoff applies on the next call.
- **Revalidation failures** never propagate to callers (they got the stale value). `on_revalidate_error` invoked (default: `logging.exception`). `revalidate_backoff` prevents retry storms.
- **Backoff state GC.** Bounded LRU for backoff entries (default 1024); avoids unbounded leak on caches with many failing keys.
- **Context propagation.** Sync `REVALIDATE` runs factory in a thread pool → `threading.local` and request context **do not propagate**. Async `REVALIDATE` uses `asyncio.create_task` → **`contextvars` propagate**. Asymmetry documented. Pass a context-aware executor for sync if needed.

## Decorator + `cache_info()`

```python
@overload
def cached(
    cache: TypedCache[Hashable, V], *,                # typed path — static checking
    ttl: float | None = None,
    soft_ttl: float | None = None,
    stale: StalePolicy = StalePolicy.BLOCK,
    key: Callable[..., Hashable] = hashkey,
) -> Callable[[Callable[P, V]], Callable[P, V]]: ...

@overload
def cached(
    cache: Cache, *,                                  # untyped path — Any return
    ...,
) -> Callable[[Callable[P, Any]], Callable[P, Any]]: ...

@overload
def cached(
    cache: AsyncTypedCache[Hashable, V], *, ...,
) -> Callable[[Callable[P, Awaitable[V]]], Callable[P, Awaitable[V]]]: ...
```

Usage:

```python
users: TypedCache[Hashable, User] = TypedView(redis_cache, namespace="users", codec=JsonCodec(User))

@cached(cache=users, ttl=3600, soft_ttl=600, stale=StalePolicy.REVALIDATE)
def get_user(user_id: str) -> User:        # OK: returns V=User
    return User.fetch(user_id)

@cached(cache=users, ttl=3600)
def get_score(uid: str) -> float:          # ERROR at decoration: V is User, returns float
    return Stats.score(uid)

print(get_user.cache_info())
# CacheInfo(hits=1247, misses=12, regens=3, backoffs=0)
```

`CacheInfo` counters are maintained by the decorator (process-local). Backend-side counters (when the cache implements `SupportsInfo`) are exposed separately as `cache.info()`.

Sync function + async cache (or vice versa) raises at decoration time.

## Built-in `MemoryCache` (stdlib only)

```python
class MemoryCache(Cache, SupportsAdd, SupportsEnvelope, SupportsInfo):
    """OrderedDict-backed LRU. Thread-safe via a single lock.
    Suitable for tests, small services, and zero-deps environments.
    For higher throughput, use freshcache.adapters.cachebox.CacheboxAdapter."""

    def __init__(self, maxsize: int = 1024): ...
```

Implements the transport-level `Cache` Protocol. To use it as a typed view, wrap with `TypedView`:

```python
local: Cache = MemoryCache(maxsize=1024)
users: TypedCache[str, User] = TypedView(local, namespace="users")
```

Hard TTL enforced via a `hard_expires_at` attribute tracked alongside `OrderedDict` entries. Lazy expiry on read; no background sweep in v0.1.

## Adapters

Each ~30 LOC. One file per supported library; guarded imports.

```python
# freshcache/adapters/redis.py
try:
    import redis
except ImportError as e:
    raise ImportError("RedisAdapter requires: pip install freshcache[redis]") from e

class RedisAdapter(Cache, SupportsAdd, SupportsEnvelope):
    def __init__(self, client: redis.Redis, *, namespace: str = "fc"):
        self._c, self._ns = client, namespace
        # NOTE: no codec here — adapters are transport-only.
        # Serialization lives in TypedView's codec. RedisAdapter stores bytes.

    def _k(self, key):
        h = hashlib.blake2b(repr(key).encode(), digest_size=16).hexdigest()
        return f"{self._ns}:{h}"

    def get(self, key, /, *, default=None):
        raw = self._c.get(self._k(key))
        return default if raw is None else self._unwrap(raw)

    def set(self, key, value, /, *, ttl=None, soft_ttl=None):
        payload = self._wrap(value, soft_ttl)        # Envelope if soft_ttl else raw
        self._c.set(self._k(key), payload, ex=int(ttl) if ttl else None)

    # _wrap / _unwrap handle Envelope on/off; values are already bytes from the codec
    # ...
```

`AsyncRedisAdapter` mirrors with `redis.asyncio`. Adapters for `cachebox.LRUCache` and `cachetools.LRUCache` (with lock) are shorter — those backends already provide LRU + maxsize; the adapter maps method names and handles Envelope.

**Crucial point:** adapters are transport-only. They no longer own serialization. `TypedView` owns it via the `Codec`. This makes the adapters simpler and lets users mix codecs over one connection.

For users who want the old behavior (raw access, no typed view), `RedisAdapter` works directly with `Cache.get`/`set`, storing whatever bytes the user provides.

## Dependencies

```toml
[project]
name = "freshcache"
requires-python = ">=3.13"                       # PEP 696 TypeVar defaults are stdlib
dependencies = []                                # zero by default

[project.optional-dependencies]
cachebox   = ["cachebox>=4.0"]
cachetools = ["cachetools>=5.0"]
redis      = ["redis>=5.0"]                      # install redis[hiredis] in prod
msgpack    = ["msgpack>=1.0"]
zstd       = ["zstandard>=0.22"]
dev        = ["pytest", "pytest-asyncio", "pytest-cov", "hypothesis",
              "mypy", "pyright", "ruff", "fakeredis", "cachebox", "redis",
              "msgpack", "zstandard"]
```

Combinations:

- `pip install freshcache` — pure-Python, MemoryCache + NullCache, in-process only.
- `pip install freshcache[redis]` — adds Redis adapter. Production should also `pip install redis[hiredis]` for the C parser (~2–3× faster parsing).
- `pip install freshcache[cachebox]` — faster local cache via cachebox.

### Why these libraries

- **`redis>=5.0`**: Official client. Sync + async unified into one package since 5.0. We don't pull in `hiredis` as a hard dependency because some environments forbid C extensions, but we recommend `pip install redis[hiredis]` in production docs.
- **`cachebox` (optional)**: Rust-backed LRU/TTL, thread-safe by default. Made optional so adoption isn't blocked in binary-restricted environments; stdlib `MemoryCache` is the fallback.
- **`cachetools` (optional)**: For users who already depend on it; adapter rather than reinventing.
- **`msgpack` (optional)**: For cross-language interop or compact storage.

### Explicitly not pulled in

- `attrs`/`pydantic` — overkill for `Envelope` and `Result`. Stdlib `dataclasses(slots=True)` enough.
- `anyio` — would bleed into the public surface. Keep sync/async explicit.
- `structlog`/`loguru` — libraries shouldn't pick a logger.
- `tenacity` — retry policy belongs to the caller.
- `aiocache` — mostly a thin wrapper around `redis-py` + a plugin system we wouldn't use; less maintained.
- `dogpile.cache` — sync-only, region-oriented API doesn't fit Protocol shape. Steal the ideas (single-flight, stale-while-revalidate), not the code.

## Namespace versioning pattern (documented, not enforced)

When the meaning of a cached value changes (you ship code that returns a different shape for the same inputs), every existing cached entry is now wrong. Common solution: include a schema version in the `TypedView`'s namespace.

```python
SCHEMA_VERSION = 3   # bump when User's shape changes

users: TypedCache[str, User] = TypedView(
    redis_cache,
    namespace=f"v{SCHEMA_VERSION}:users",
    codec=JsonCodec(User),
)

@cached(cache=users, ttl=3600)
def get_user(user_id: str) -> User: ...
```

After deploy, the new code reads/writes under `v3:users:…`; old `v2:users:…` entries linger until their TTL expires. No coordination between deploys; no stampede.

This is a deploy-time concern, not a library concern, but easy to get wrong without an example.

## Consistency model

| Operation | Guarantee |
|-----------|-----------|
| `get` after `set` (same process, same cache) | Read-your-writes |
| `get` after `set` (different process, shared backend) | Eventually consistent — typically <10ms on Redis on a healthy network |
| `set` after `set` (concurrent) | Last-writer-wins. Use `add` (where supported) for first-writer-wins. |
| `delete` | Idempotent. Returns existence at the moment of the call. |
| **Hard TTL** | Best-effort. Redis enforces server-side; in-memory backends enforce lazily on read. May expire early under memory pressure / eviction. |
| **Soft TTL** | Wall-clock-based. Cross-process boundary subject to **NTP drift** (often seconds). Acceptable for stampede mitigation; **not for security-relevant freshness**. |
| `SingleFlight.get_or_create` on miss | At most one concurrent `factory` per key per process. Others block. |
| `SingleFlight.get_or_create` with `BLOCK` on stale | Same as miss. |
| `SingleFlight.get_or_create` with `SERVE` on stale | Returns immediately. No regeneration. |
| `SingleFlight.get_or_create` with `REVALIDATE` on stale | Returns stale immediately. At most one background regen per key per process. Cross-process: N processes may each spawn one. Use `DistributedSingleFlight` (v0.2) for fleet-wide single-flight. |
| Revalidation failure | Stale value remains served until hard TTL. `on_revalidate_error` invoked. Backoff prevents retry storm. |
| Backend failure on `get` | Fail-open by default → returns `default`, decorator recomputes. Configurable per-adapter. |
| Backend failure on `set` | Fail-open by default → logs, continues. |
| Context propagation across revalidation | Sync: **no** propagation of `threading.local` / request context. Async: **yes** propagation of `contextvars`. Asymmetry is intentional and documented. |
| Codec errors on read | `get` returns `default`. `get_strict` raises `CodecError`. Caller can interpret as "incompatible cached blob, treat as miss." |

Explicitly *not* guaranteed:

- Cache coherence across different backends.
- Transactional multi-key operations.
- Invalidation propagation between independent caches.
- Exactly-once factory execution across the fleet under `REVALIDATE` (requires `DistributedSingleFlight`).

## Package layout

```
freshcache/
  __init__.py              # public re-exports
  protocols.py             # Cache, AsyncCache, Supports* markers
  typed.py                 # TypedCache, AsyncTypedCache Protocols + TypedView
  envelope.py              # Envelope dataclass
  policies.py              # StalePolicy, Result, CacheInfo
  keys.py                  # hashkey, default_key_encoder, namespace helpers
  memory.py                # MemoryCache, NullCache
  codecs/
    __init__.py            # Codec Protocol, CodecError, PickleCodec, JsonCodec, GzipCodec
    msgpack.py             # MsgpackCodec (optional dep)
    zstd.py                # ZstdCodec (optional dep)
  singleflight/
    __init__.py            # SingleFlight (sync)
    aio.py                 # AsyncSingleFlight
  decorators.py            # @cached
  adapters/
    cachebox.py            # CacheboxAdapter (optional)
    cachetools.py          # CachetoolsAdapter (optional)
    redis.py               # RedisAdapter (optional)
    aredis.py              # AsyncRedisAdapter (optional)
  py.typed
```

## v0.1 scope

Ship:

1. `Cache` + `AsyncCache` Protocols (transport).
2. `TypedCache` + `AsyncTypedCache` Protocols (typed view).
3. Capability markers (`SupportsAdd`, `SupportsEnvelope`, `SupportsInfo`) + typed mirrors.
4. `TypedView` + `AsyncTypedView` concrete bridges.
5. `Codec` Protocol + `PickleCodec` (default), `JsonCodec`, `GzipCodec`, `ZstdCodec` (extra), `CodecError`.
6. `Envelope` (used only when `soft_ttl` is set).
7. Built-in `MemoryCache` (stdlib only) + `NullCache`.
8. `RedisAdapter` + `AsyncRedisAdapter`.
9. `CacheboxAdapter` + `CachetoolsAdapter`.
10. `SingleFlight` + `AsyncSingleFlight` with all three `StalePolicy` modes.
11. `@cached` with `stale=` parameter, `cache_info()`, and static return-type checking against `TypedCache`.
12. `CONSISTENCY.md` matching the table.
13. Namespace versioning docs.
14. Property tests for LRU + soft/hard TTL + single-flight invariants.

Defer to v0.2+:

- **`TieredCache` / `CompositeCache`** — L1 (memory) + L2 (Redis) hierarchy with read-through and write-through policies. Production-essential but composable; users can build it from primitives in the meantime.
- **`Hooks` Protocol** for observability — `on_hit` / `on_miss` / `on_regen_start` / `on_regen_end` / `on_error` etc. Lets users plug into Prometheus, OpenTelemetry, structlog, anything. v0.1 ships counter-based `cache_info()` only.
- `FallbackCache` — primary cache with fallback on errors (e.g., Redis down → fall through to local memory).
- `DistributedSingleFlight` — Redis-backed fleet-wide single-flight via SET NX + fencing tokens.
- Memcached adapter.
- `@cachedmethod` for instance methods.
- Region-level / generation-counter invalidation.
- `MultiCache` protocol (`get_multi`/`set_multi`) for pipelining.
- Periodic sweep for expired entries in memory backend.
- `PydanticCodec` as a first-party codec.

## Open questions

1. **Executor lifecycle for sync `REVALIDATE`.** Default lazily-created `ThreadPoolExecutor(max_workers=4)` with `SingleFlight.close()` for clean shutdown? Or require explicit executor? Lean lazy-create.
2. **Bounded backoff LRU size.** Default 1024 entries; configurable. Above that, oldest backoff entries are forgotten — those keys can retry sooner than `revalidate_backoff` suggests. Document.
3. **Key type for `TypedCache[K, V]`.** Allow arbitrary `Hashable` (loose; depends on `repr(key)` being stable for `default_key_encoder`) or restrict to `str | int | bytes | tuple-of-same` (strict). Lean loose with documented requirement.
4. **`TypedView` validation cost.** `validate=True` does `isinstance(value, value_type)` on `set`. Cheap for primitives, expensive for nested types. Off by default; doc as dev/test aid.
5. **Type-checker parity.** `Protocol` with `@overload`ed methods + generics is the most divergent area between mypy and pyright. 30-minute spike on both before committing; simplify if either rejects.
6. **`SupportsEnvelope` for adapters that can't natively wrap.** Memcached future adapter would always serialize; envelope always wrapped when `soft_ttl` set. Acceptable trade.

## Prior art credited

- **`cachetools`**: the `Cache` shape and `@cached` decorator ergonomics. We diverge by splitting transport vs. typed view and adding async.
- **`cachebox`**: optional fast adapter for the memory backend.
- **`dogpile.cache`**: single-flight (`get_or_create`), stale-while-revalidate. Region API doesn't translate; semantics do.
- **`aiocache`**: confirmed that a generic plugin system is more weight than value at this scope. We adapt `redis.asyncio` directly instead.
- **`functools.lru_cache`**: thread-safe by default, the ergonomic bar for `@cached`.
- **`typing.SupportsInt` / `SupportsAbs`**: the capability-marker Protocol pattern.
- **Generic typed views over untyped storage**: pattern echoed in TypeScript's `Redis<T>` wrappers and Go's typed cache libraries; mature idea, new to Python caching.
