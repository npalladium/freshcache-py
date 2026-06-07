"""Codec behavior."""

from __future__ import annotations

import pytest

from freshcache import CodecError, GzipCodec, JsonCodec, PickleCodec


def test_pickle_roundtrip():
    c = PickleCodec()
    data = {"a": 1, "b": [1, 2, 3], "c": (4, 5)}
    blob = c.dumps(data)
    assert c.loads(blob) == data


def test_pickle_decode_garbage_raises_codec_error():
    c = PickleCodec()
    with pytest.raises(CodecError):
        c.loads(b"not a pickle blob")


def test_json_roundtrip():
    c = JsonCodec()
    data = {"a": 1, "b": [1, 2, 3]}
    blob = c.dumps(data)
    assert c.loads(blob) == data


def test_json_rejects_non_json_types():
    c = JsonCodec()
    with pytest.raises(CodecError):
        c.dumps({"k": object()})


def test_json_decode_invalid():
    c = JsonCodec()
    with pytest.raises(CodecError):
        c.loads(b"not json")


def test_gzip_composition_with_json():
    c = GzipCodec(JsonCodec())
    data = {"long": "x" * 10000}
    blob = c.dumps(data)
    assert len(blob) < 10000  # actually compressed
    assert c.loads(blob) == data


def test_gzip_invalid_blob_raises():
    c = GzipCodec(JsonCodec())
    with pytest.raises(CodecError):
        c.loads(b"not gzip")


def test_gzip_inner_decoder_error_propagates():
    # Valid gzip of invalid JSON.
    import gzip
    invalid = gzip.compress(b"not json")
    c = GzipCodec(JsonCodec())
    with pytest.raises(CodecError):
        c.loads(invalid)
