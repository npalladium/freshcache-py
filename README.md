# freshcache

A narrowly-scoped Python caching library: **typed Protocol layering +
single-flight with stale-while-revalidate**. Bring your own backend.

```
┌─────────────────────────────────────────────────────────────┐
│  @cached(stale=…)         sync + async, type-inferred       │
├─────────────────────────────────────────────────────────────┤
│  SingleFlight    BLOCK / SERVE / REVALIDATE policies        │
├─────────────────────────────────────────────────────────────┤
│  TypedCache[K, V]    typed view: codec + namespace          │
├─────────────────────────────────────────────────────────────┤
│  Cache (transport)   SupportsAdd / SupportsEnvelope / …     │
├─────────────────────────────────────────────────────────────┤
│  Adapters: redis / cachebox / cachetools  +  MemoryCache    │
└─────────────────────────────────────────────────────────────┘
```

## Install

```sh
pip install freshcache              # MemoryCache + NullCache, zero deps
pip install freshcache[redis]       # + RedisAdapter / AsyncRedisAdapter
pip install freshcache[cachebox]    # + Rust-backed local cache
pip install freshcache[cachetools]  # + adapter for cachetools.Cache
```

Python 3.13+.

## 60-second tour

```python
from dataclasses import dataclass
from freshcache import MemoryCache, TypedView, cached, StalePolicy

@dataclass(frozen=True)
class User:
    name: str
    age: int

backend = MemoryCache(maxsize=1024)
users: TypedView[str, User] = TypedView(backend, namespace="users")

@cached(cache=users, ttl=3600, soft_ttl=600, stale=StalePolicy.REVALIDATE)
def get_user(uid: str) -> User:
    return User(uid, 30)   # imagine fetching from a slow source

get_user("alice")              # miss → fetch
get_user("alice")              # hit
print(get_user.cache_info())   # DecoratorInfo(hits=1, misses=1, regens=0, backoffs=0)
```

Static type checking catches mismatches at decoration time:

```python
@cached(cache=users)
def get_score(uid: str) -> float:    # mypy/pyright: V is User, not float ❌
    ...
```

## Redis

```python
import redis
from freshcache import TypedView, JsonCodec
from freshcache.adapters.redis import RedisAdapter

client = redis.Redis()
backend = RedisAdapter(client, namespace="prod")
users: TypedView[str, User] = TypedView(
    backend, namespace="users", codec=JsonCodec()
)
```

Async mirror in `freshcache.adapters.aredis.AsyncRedisAdapter`.

## What freshcache is *not*

- Not a storage engine — LRU / LFU / eviction is delegated to backends.
- Not a Redis client — we adapt `redis-py`.
- Not a replacement for `functools.lru_cache` on sub-microsecond hot paths.

See [DESIGN.md](DESIGN.md) for the full design rationale and
[CONSISTENCY.md](CONSISTENCY.md) for guarantees.
