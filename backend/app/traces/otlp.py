"""Rewrite the resource of every span batch in an OTLP/protobuf ExportTraceServiceRequest, and nothing else.

Why by hand: the trusted ingestion path must replace the sender's identity attributes with the authenticated service's
identity. Only the Resource message (field 1 of each ResourceSpans) is re-encoded; every other byte is copied unchanged, so
spans, events, links and status are never altered. No protobuf dependency is needed for this (the wire format is small and
the fields used are fixed by opentelemetry-proto: ExportTraceServiceRequest.resource_spans=1, ResourceSpans.resource=1,
Resource.attributes=1, KeyValue.key=1, KeyValue.value=2, AnyValue.string_value=1).

Tests decode the output with the official opentelemetry-proto package to check the result independently.
"""
from __future__ import annotations

from collections.abc import Callable, Iterator

OWNED_PREFIX = "uptimetrace."
OWNED_KEYS = frozenset({"service.name"})


class WireError(ValueError):
    """The bytes are not a well-formed OTLP trace export request."""


def _read_varint(buf: bytes, i: int) -> tuple[int, int]:
    result = shift = 0
    while True:
        if i >= len(buf) or shift > 63:
            raise WireError("truncated or oversized varint")
        b = buf[i]
        i += 1
        result |= (b & 0x7F) << shift
        if not b & 0x80:
            return result, i
        shift += 7


def _varint(n: int) -> bytes:
    out = bytearray()
    while True:
        b = n & 0x7F
        n >>= 7
        if n:
            out.append(b | 0x80)
        else:
            out.append(b)
            return bytes(out)


def _fields(buf: bytes) -> Iterator[tuple[int, int, bytes]]:
    """Yield (field number, wire type, the complete encoded field including its tag)."""
    i = 0
    while i < len(buf):
        start = i
        tag, i = _read_varint(buf, i)
        field, wt = tag >> 3, tag & 7
        if field == 0:
            raise WireError("field number 0")
        if wt == 0:
            _, i = _read_varint(buf, i)
        elif wt == 1:
            i += 8
        elif wt == 5:
            i += 4
        elif wt == 2:
            length, i = _read_varint(buf, i)
            i += length
        else:
            raise WireError(f"unsupported wire type {wt}")
        if i > len(buf):
            raise WireError("field runs past the end of the message")
        yield field, wt, buf[start:i]


def _payload(encoded_field: bytes) -> bytes:
    """The value of a length-delimited field, given the whole encoded field."""
    tag, i = _read_varint(encoded_field, 0)
    length, i = _read_varint(encoded_field, i)
    return encoded_field[i:i + length]


def _len_field(field: int, payload: bytes) -> bytes:
    return _varint((field << 3) | 2) + _varint(len(payload)) + payload


def _string_field(field: int, text: str) -> bytes:
    return _len_field(field, text.encode("utf-8"))


def _key_value(key: str, value: str) -> bytes:
    any_value = _string_field(1, value)                       # AnyValue.string_value
    return _string_field(1, key) + _len_field(2, any_value)  # KeyValue.key, KeyValue.value


def _key_of(key_value: bytes) -> str:
    for field, wt, enc in _fields(key_value):
        if field == 1 and wt == 2:
            return _payload(enc).decode("utf-8", "strict")
    return ""


def _rewrite_resource(resource: bytes, owned: dict[str, str]) -> bytes:
    kept: list[bytes] = []
    other: list[bytes] = []
    for field, wt, enc in _fields(resource):
        if field == 1 and wt == 2:                            # Resource.attributes
            key = _key_of(_payload(enc))
            if key.startswith(OWNED_PREFIX) or key in OWNED_KEYS or key in owned:
                continue                                       # sender-supplied identity: dropped
            kept.append(enc)
        else:
            other.append(enc)                                  # dropped_attributes_count etc., unchanged
    attrs = kept + [_len_field(1, _key_value(k, v)) for k, v in owned.items()]
    return b"".join(other) + b"".join(attrs)


def rewrite_request(body: bytes, owned: dict[str, str], transform: Callable[[bytes], bytes] | None = None) -> bytes:
    """Return `body` with every resource's identity attributes replaced by `owned` (the authenticated identity).

    Resources without any span batch are not created. A batch without a resource gets one, carrying `owned`."""
    out = bytearray()
    for field, wt, enc in _fields(body):
        if field == 1 and wt == 2:                            # ResourceSpans
            inner = _payload(enc)
            new_inner = bytearray()
            has_resource = False
            for f2, wt2, enc2 in _fields(inner):
                if f2 == 1 and wt2 == 2:                      # ResourceSpans.resource
                    has_resource = True
                    new_inner += _len_field(1, _rewrite_resource(_payload(enc2), owned))
                else:
                    new_inner += enc2
            if not has_resource:
                new_inner = bytearray(_len_field(1, _rewrite_resource(b"", owned))) + new_inner
            out += _len_field(1, bytes(new_inner))
        else:
            out += enc                                        # anything else in the request is copied as received
    return bytes(out)
