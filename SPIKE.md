# Type-checker spike — findings (v2, expanded coverage)

Goal: validate the full design in `DESIGN.md` under both `mypy --strict` and `pyright --strict`, since `Protocol + overloads + generics + ParamSpec + capability markers + runtime_checkable + async + PEP 696 defaults` is the most divergent corner of Python typing.

## Result: design is shippable as written

| Check | Result |
|---|---|
| `mypy --strict` on 12 source files | **0 errors** |
| `pyright --strict` on 12 source files | **0 errors** |
| Negative tests catch wrong-V `set()` | Both checkers, with clear messages |
| Negative tests catch wrong-return decorated function | Both checkers, with clear messages |

The two `# pyright: ignore` markers in `memory.py` for transport-layer `Any` propagation are the only suppressions. `TypedCache` / `TypedView` / `SingleFlight` / `Codec` paths have zero suppressions.

## What the spike now covers

Original spike (v1) plus all four extension areas:

| Feature | Spiked? | Result |
|---|---|---|
| `@runtime_checkable Protocol` | ✓ | Both clean |
| Generic `Protocol[K, V]` with `bound=Hashable, contravariant=True` | ✓ | Both clean |
| `@overload` methods on Protocol (3 overloads on `get`) | ✓ | Both clean |
| Capability markers (`SupportsAdd`, `SupportsEnvelope`) | ✓ | Both clean |
| `isinstance()` narrowing on `runtime_checkable` Protocols | ✓ | Both clean |
| `ParamSpec` on decorator | ✓ | Both clean |
| Overloaded decorator dispatching on Protocol arg | ✓ | Both clean |
| Structural typing (concrete classes satisfy Protocol without `: Cache`) | ✓ | Both clean |
| **Async Protocols** (`AsyncCache`, `AsyncTypedCache`) | ✓ | Both clean |
| **`SingleFlight[K, V]`** with `Generic[K, V]` + `StalePolicy` enum | ✓ | Both clean |
| **Codec composition** (`GzipCodec(JsonCodec())`) typed via Protocol | ✓ | Both clean |
| **PEP 696 TypeVar defaults** (stdlib on 3.13+) | ✓ | Both clean |
| **Negative cases** (wrong V, wrong return type) | ✓ | Both catch correctly |

## Findings that changed the design

### 1. `K` must be contravariant in `TypedCache`

Both checkers flagged `TypeVar("K", bound=Hashable)` as needing contravariance — only used in input positions. Fixed:

```python
K = TypeVar("K", bound=Hashable, contravariant=True)
```

Type-theoretically correct: `TypedCache[Hashable, V]` is a subtype of `TypedCache[str, V]`.

### 2. Decorator overload must be generic on K, not concrete

Initial sketch had `cache: TypedCache[Hashable, V]`. mypy rejects `TypedCache[str, User]` against it.

Fixed by making `cached` generic on K:

```python
K = TypeVar("K", bound=Hashable)

@overload
def cached(cache: TypedCache[K, V], *, ...) -> Callable[[Callable[P, V]], Callable[P, V]]: ...
```

K is inferred from the cache at the call site.

## Findings that did NOT change the design

### 3. PEP 696 TypeVar defaults work cleanly in both checkers (stdlib on 3.13+)

Both `mypy --strict` and `pyright --strict` accept stdlib `typing.TypeVar` with `default=`:

```python
from typing import TypeVar

K = TypeVar("K", bound=Hashable, contravariant=True, default=Hashable)
V = TypeVar("V", default=Any)

class TypedCacheD(Protocol[K, V]): ...

cache_default: TypedCacheD = _ConcreteCache()       # K=Hashable, V=Any inferred
cache_typed:   TypedCacheD[str, int] = _ConcreteCache()
```

**Recommendation**: use defaults in v0.1. Zero deps (stdlib on 3.13+). Strict-mode checkers no longer warn about unparameterized generics at instantiation.

### 4. Async protocols are structurally a free upgrade

`AsyncCache` and `AsyncTypedCache` are identical in shape to their sync counterparts, only with `async def`. Both checkers handle them with no special configuration. No design decisions needed — just write them.

### 5. `SingleFlight[K, V]` types cleanly with `Generic[K, V]`

Standard generic class — no Protocol gymnastics needed. The `Cache & SupportsEnvelope` runtime intersection is expressed as: take `Cache`, runtime `isinstance(inner, SupportsEnvelope)` check, store both references. Types flow through naturally.

### 6. Codec composition is just nested constructors

`GzipCodec(JsonCodec())` — each codec implements the `Codec` Protocol; composition is constructor wrapping. Both checkers infer `Codec` for the result. No special variance needed (codecs are `Codec[Any]`-typed by design).

## Findings about strict-mode quirks (not blockers)

### 7. Pyright strict treats `Any` access as `Unknown`

Pyright strict reports `Unknown` errors whenever `Any` propagates through attribute access:

```python
env: Envelope[Any]
env.value      # pyright strict: "Type of value is unknown"
```

`Cache` and `MemoryCache` operate on `Any` by design (transport layer). Mitigated with two narrow `# pyright: ignore` markers in `memory.py`. `TypedCache` / `TypedView` / `SingleFlight[K, V]` (where V is concrete) have zero such issues.

**Mitigation options for adopters:**
- Accept the per-line ignores (what we did).
- Use pyright `basic` mode (default) — Any propagation is fine.
- Disable `reportUnknownMemberType` and `reportUnknownVariableType` for `freshcache.memory` specifically.

### 8. IDE/LSP pyright diverges from CLI pyright

The IDE LSP pyright shows persistent "Import freshcache.* could not be resolved" errors even after editable install. CLI pyright resolves all imports cleanly. This is a known LSP cache issue — not a design problem. Restarting the language server (or running pyright from CLI) gives the authoritative result.

### 9. The "overlapping overload" warning is benign (IDE only)

IDE pyright sometimes warns that the decorator's two overloads (`cache: TypedCache[K, V]` and `cache: Cache`) overlap. CLI pyright in strict mode accepts them cleanly. Dispatch is correct at all call sites: passing a typed cache matches overload 1; passing a bare cache matches overload 2.

## Negative-test details

Both checkers correctly reject the EXPECT-ERROR cases:

```python
users: TypedCache[str, User] = TypedView(backend, namespace="users")

users.set("alice", 42)
# mypy:    Argument 2 to "set" of "TypedCache" has incompatible type "int"; expected "User"
# pyright: "Literal[42]" is not assignable to "User"

@cached(cache=users, ttl=3600)
def get_score(user_id: str) -> float: ...
# mypy:    Argument 1 has incompatible type "Callable[[str], float]"; expected "Callable[[str], User]"
# pyright: Function return type "float" is incompatible with type "User"
```

Errors are reported at the call/decoration site in both checkers — IDEs underline the right line.

## What the spike still doesn't cover

- **`asyncio.Lock` / `asyncio.Event` in `AsyncSingleFlight`.** Trivial extension; nothing fundamentally new typing-wise.
- **`TypedSupportsAdd` / `TypedSupportsEnvelope` typed capability markers.** Mirrors of `SupportsAdd` / `SupportsEnvelope` with K, V. Should be a 5-minute extension.
- **Real adapters (`RedisAdapter`, `CacheboxAdapter`).** Implementation-shaped; would touch real APIs but no new typing patterns.
- **`Hooks` Protocol** (v0.2). Same shape as other Protocols; not a typing risk.

## Recommendation

**Ship the design as written, including PEP 696 defaults.**

Locked-in design decisions:
1. `K` is contravariant in `TypedCache`.
2. `@cached` is generic on K.
3. PEP 696 TypeVar defaults via stdlib `typing` (min Python 3.13+).
4. Capability markers stay on `Cache`; typed mirrors only when SingleFlight needs them.
5. Pyright strict is supported via 2 narrow ignores in `memory.py`; everything else is clean.

## Reproduction

```sh
uv sync
# Positive cases
uv run mypy --strict src/freshcache tests/
uv run pyright src/freshcache tests/

# Negative cases (expect 2 errors in each)
uv run mypy --strict tests/test_types_negative.py
uv run pyright tests/test_types_negative.py
```

## File inventory

```
src/freshcache/
  protocols.py        # Cache, AsyncCache, SupportsAdd, SupportsEnvelope,
                      # TypedCache, AsyncTypedCache
  envelope.py         # Envelope[V] dataclass
  codecs.py           # Codec Protocol, PickleCodec, JsonCodec, GzipCodec, CodecError
  singleflight.py     # SingleFlight[K, V], StalePolicy, Result[V]
  memory.py           # MemoryCache (implements Cache + SupportsAdd + SupportsEnvelope)
  typed.py            # TypedView[K, V] (implements TypedCache[K, V])
  decorators.py       # @cached with overloads + ParamSpec
  __init__.py         # public re-exports

tests/
  test_types.py             # positive cases — assert_type for every overload
  test_types_negative.py    # negative cases — both checkers catch
  test_pep696.py            # TypeVar defaults via typing_extensions

pyrightconfig.json    # strict mode, py 3.13, src + tests
pyproject.toml        # mypy + pyright as dev deps; requires-python = ">=3.13"
```
