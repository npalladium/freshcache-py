"""RedisAdapter: transport-only Cache backed by redis-py 5.x.

Stores bytes. The TypedView layer owns codec-level (de)serialization.
Envelope wrapping happens here when soft_ttl is set, using a thin binary
header so we don't double-pickle codec output.

Wire format for soft-TTL entries:

    b"E1\\n<created_at_float>\\n<soft_ttl_float>\\n" + payload

For raw entries (no soft_ttl):

    b"R1\\n" + payload

The first two bytes are a magic; the trailing payload is exactly what the
caller passed to .set() (bytes). On read, we strip and reconstruct.
"""

from __future__ import annotations

import hashlib
import pickle
import re
import time
from collections.abc import Callable, Hashable
from typing import Any

try:
    from redis import Redis
except ImportError as e:  # pragma: no cover
    raise ImportError(
        "RedisAdapter requires the 'redis' extra: pip install freshcache[redis]"
    ) from e

from freshcache.envelope import Envelope


# ----- key encoding -----

# Characters allowed in pass-through string keys. Excludes:
#   '#'   — reserved for our hash-prefix marker
#   '{}'  — Redis cluster hashtag syntax (avoids accidental co-location)
#   space / control / non-ASCII — keeps redis-cli grep usable
_SAFE_KEY = re.compile(r"^[A-Za-z0-9_./@:\-]{1,200}$")


def _is_safe_str(s: str) -> bool:
    return bool(_SAFE_KEY.match(s))


def _stable_bytes(key: Hashable) -> bytes:
    """Stable serialization of a Hashable for hashing into a Redis key.

    Uses pickle protocol 5. Caveat: pickled `frozenset` iteration order is
    implementation-defined and depends on PYTHONHASHSEED, so frozenset keys
    may produce different bytes across processes. Document for users.
    """
    try:
        return pickle.dumps(key, protocol=5)
    except (TypeError, AttributeError, pickle.PicklingError) as e:
        raise TypeError(
            f"key of type {type(key).__name__} cannot be serialized for "
            "Redis; pass key_serializer= to override"
        ) from e


def default_key_suffix(key: Hashable) -> str:
    """Hybrid: pass through safe string keys; hash everything else.

    Safe = `[A-Za-z0-9_./@:-]` only, length <= 200, no `#`/`{}`/whitespace.
    Anything else gets blake2b'd into a 32-char hex digest with a `#h:` prefix.
    """
    if isinstance(key, str) and _is_safe_str(key):
        return key
    blob = _stable_bytes(key)
    return "#h:" + hashlib.blake2b(blob, digest_size=16).hexdigest()




_RAW = b"R1"
_ENV = b"E1"


def wire_wrap(value: bytes, soft_ttl: float | None) -> bytes:
    """Wrap codec-produced bytes in our Redis wire format.

    Module-internal; shared by the sync and async Redis adapters.
    """
    if soft_ttl is None:
        return _RAW + b"\n" + value
    return (
        _ENV
        + b"\n"
        + f"{time.time()}".encode()
        + b"\n"
        + f"{soft_ttl}".encode()
        + b"\n"
        + value
    )


def wire_unwrap(blob: bytes) -> tuple[bytes, float | None, float | None]:
    """Unwrap a wire blob. Returns (payload, created_at, soft_ttl).

    Timestamps None when entry was raw. Module-internal.
    """
    if blob.startswith(_RAW + b"\n"):
        return blob[len(_RAW) + 1 :], None, None
    if blob.startswith(_ENV + b"\n"):
        rest = blob[len(_ENV) + 1 :]
        created_b, _, rest = rest.partition(b"\n")
        soft_ttl_b, _, payload = rest.partition(b"\n")
        try:
            return payload, float(created_b), float(soft_ttl_b)
        except ValueError:
            return blob, None, None
    return blob, None, None


def as_bytes(x: bytes | str | memoryview | bytearray) -> bytes:
    """Coerce redis-py return shapes (bytes | str) to bytes."""
    if isinstance(x, bytes):
        return x
    if isinstance(x, str):
        return x.encode()
    return bytes(x)


class RedisAdapter:
    """Transport-only Redis-backed Cache. Stores bytes; codec lives in TypedView.

    Key encoding (`key_serializer=`):

    - Default: safe ASCII string keys pass through unchanged (`users:alice`
      stays readable in `redis-cli`); everything else is blake2b-hashed with
      a `#h:` prefix.
    - Override with `key_serializer=` to take full control of the suffix
      (e.g. ``key_serializer=lambda k: msgpack.packb(k).hex()`` for
      cross-language interop).
    """

    def __init__(
        self,
        client: Redis,
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

    def get(self, key: Hashable, /, *, default: Any = None) -> Any:
        raw = self._c.get(self._k(key))
        if raw is None:
            return default
        payload, _, _ = wire_unwrap(as_bytes(raw))
        return payload

    def get_strict(self, key: Hashable, /) -> Any:
        raw = self._c.get(self._k(key))
        if raw is None:
            raise KeyError(key)
        payload, _, _ = wire_unwrap(as_bytes(raw))
        return payload

    def get_envelope(self, key: Hashable, /) -> Envelope[Any] | None:
        raw = self._c.get(self._k(key))
        if raw is None:
            return None
        payload, created, soft_ttl = wire_unwrap(as_bytes(raw))
        if created is None or soft_ttl is None:
            return None
        return Envelope(payload, created, soft_ttl)

    def set(
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
                "RedisAdapter stores bytes only. Wrap with TypedView for "
                "value serialization."
            )
        blob = wire_wrap(bytes(value), soft_ttl)
        kwargs: dict[str, Any] = {}
        if ttl is not None and ttl > 0:
            kwargs["px"] = max(1, int(ttl * 1000))
        self._c.set(self._k(key), blob, **kwargs)

    def add(
        self,
        key: Hashable,
        value: Any,
        /,
        *,
        ttl: float | None = None,
        soft_ttl: float | None = None,
    ) -> bool:
        if not isinstance(value, (bytes, bytearray)):
            raise TypeError("RedisAdapter stores bytes only.")
        blob = wire_wrap(bytes(value), soft_ttl)
        kwargs: dict[str, Any] = {"nx": True}
        if ttl is not None and ttl > 0:
            kwargs["px"] = max(1, int(ttl * 1000))
        result = self._c.set(self._k(key), blob, **kwargs)
        return bool(result)

    def delete(self, key: Hashable, /) -> bool:
        n = self._c.delete(self._k(key))
        try:
            return int(n) > 0
        except (TypeError, ValueError):
            return bool(n)
