"""Codecs: pluggable serialization. Spike for Protocol-typed codecs + composition."""

from __future__ import annotations

import gzip
import json
import pickle
from typing import Any, Protocol, runtime_checkable


class CodecError(ValueError):
    """Raised when a codec cannot deserialize a blob."""


@runtime_checkable
class Codec(Protocol):
    """Pluggable serialization. Codec[Any] — V is enforced at the TypedView boundary."""

    def dumps(self, value: Any, /) -> bytes: ...
    def loads(self, blob: bytes, /) -> Any: ...


class PickleCodec:
    """Default codec. Handles any Python object. Don't use across trust boundaries."""

    def dumps(self, value: Any, /) -> bytes:
        return pickle.dumps(value)

    def loads(self, blob: bytes, /) -> Any:
        try:
            return pickle.loads(blob)
        except (pickle.UnpicklingError, EOFError, AttributeError) as e:
            raise CodecError("pickle decode failed") from e


class JsonCodec:
    """JSON codec. Restricts to JSON-shape data."""

    def dumps(self, value: Any, /) -> bytes:
        try:
            return json.dumps(value).encode("utf-8")
        except (TypeError, ValueError) as e:
            raise CodecError("json encode failed") from e

    def loads(self, blob: bytes, /) -> Any:
        try:
            return json.loads(blob.decode("utf-8"))
        except (ValueError, UnicodeDecodeError) as e:
            raise CodecError("json decode failed") from e


class GzipCodec:
    """Wraps another codec, gzipping its output."""

    def __init__(self, inner: Codec, level: int = 6) -> None:
        self._inner = inner
        self._level = level

    def dumps(self, value: Any, /) -> bytes:
        return gzip.compress(self._inner.dumps(value), compresslevel=self._level)

    def loads(self, blob: bytes, /) -> Any:
        try:
            decompressed = gzip.decompress(blob)
        except OSError as e:
            raise CodecError("gzip decompress failed") from e
        return self._inner.loads(decompressed)
