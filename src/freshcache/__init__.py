"""freshcache — typed Protocol caching with single-flight."""

from freshcache.async_singleflight import AsyncSingleFlight
from freshcache.async_typed import AsyncTypedView
from freshcache.codecs import Codec, CodecError, GzipCodec, JsonCodec, PickleCodec
from freshcache.decorators import DecoratorInfo, cached, hashkey
from freshcache.envelope import Envelope
from freshcache.info import CacheInfo
from freshcache.memory import MemoryCache
from freshcache.null import NullCache
from freshcache.protocols import (
    AsyncCache,
    AsyncSupportsAdd,
    AsyncSupportsEnvelope,
    AsyncTypedCache,
    Cache,
    SupportsAdd,
    SupportsEnvelope,
    SupportsInfo,
    TypedCache,
    TypedSupportsAdd,
    TypedSupportsEnvelope,
)
from freshcache.singleflight import Result, SingleFlight, StalePolicy
from freshcache.typed import TypedView

__all__ = [
    "AsyncCache",
    "AsyncSingleFlight",
    "AsyncSupportsAdd",
    "AsyncSupportsEnvelope",
    "AsyncTypedCache",
    "AsyncTypedView",
    "Cache",
    "CacheInfo",
    "Codec",
    "CodecError",
    "DecoratorInfo",
    "Envelope",
    "GzipCodec",
    "JsonCodec",
    "MemoryCache",
    "NullCache",
    "PickleCodec",
    "Result",
    "SingleFlight",
    "StalePolicy",
    "SupportsAdd",
    "SupportsEnvelope",
    "SupportsInfo",
    "TypedCache",
    "TypedSupportsAdd",
    "TypedSupportsEnvelope",
    "TypedView",
    "cached",
    "hashkey",
]
