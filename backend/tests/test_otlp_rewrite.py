"""The trusted resource rewrite: a sender's identity attributes are replaced, everything else is preserved, and malformed
input is refused. The output is decoded with the official OTLP protobuf package, so the check is independent of our code.
"""
from __future__ import annotations

import pytest
from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import ExportTraceServiceRequest
from opentelemetry.proto.common.v1.common_pb2 import AnyValue, KeyValue
from opentelemetry.proto.resource.v1.resource_pb2 import Resource
from opentelemetry.proto.trace.v1.trace_pb2 import ResourceSpans, ScopeSpans, Span

from app.traces.otlp import WireError, rewrite_request

OWNED = {"service.name": "checkout", "uptimetrace.service.id": "SERVICE-A", "uptimetrace.workspace.id": "WORKSPACE-A"}


def request_claiming(attrs: dict[str, str], *, dropped: int = 0) -> bytes:
    req = ExportTraceServiceRequest()
    rs = req.resource_spans.add()
    rs.resource.CopyFrom(Resource(attributes=[KeyValue(key=k, value=AnyValue(string_value=v)) for k, v in attrs.items()],
                                  dropped_attributes_count=dropped))
    span = rs.scope_spans.add().spans.add()
    span.trace_id, span.span_id, span.name = b"\x01" * 16, b"\x02" * 8, "GET /checkout"
    span.status.code = 2
    span.start_time_unix_nano, span.end_time_unix_nano = 1_000, 9_000_000
    return req.SerializeToString()


def decode(body: bytes) -> ExportTraceServiceRequest:
    out = ExportTraceServiceRequest()
    out.ParseFromString(body)
    return out


def attributes(req: ExportTraceServiceRequest, i: int = 0) -> dict[str, str]:
    return {a.key: a.value.string_value for a in req.resource_spans[i].resource.attributes}


def test_a_spoofed_service_identity_is_replaced_by_the_authenticated_one():
    body = request_claiming({"service.name": "payments", "uptimetrace.service.id": "SERVICE-B",
                             "uptimetrace.workspace.id": "WORKSPACE-B", "host.name": "box-1"})
    got = attributes(decode(rewrite_request(body, OWNED)))
    assert got == {"host.name": "box-1", **OWNED}


def test_unknown_uptimetrace_attributes_from_the_sender_are_dropped():
    body = request_claiming({"uptimetrace.role": "admin", "uptimetrace.anything": "x", "deployment.environment": "prod"})
    got = attributes(decode(rewrite_request(body, OWNED)))
    assert "uptimetrace.role" not in got and "uptimetrace.anything" not in got
    assert got["deployment.environment"] == "prod"


def test_spans_and_the_rest_of_the_resource_are_unchanged():
    original = decode(request_claiming({"service.name": "x"}, dropped=7))
    rewritten = decode(rewrite_request(original.SerializeToString(), OWNED))
    assert rewritten.resource_spans[0].resource.dropped_attributes_count == 7
    assert rewritten.resource_spans[0].scope_spans[0].spans[0] == original.resource_spans[0].scope_spans[0].spans[0]


def test_a_batch_without_a_resource_gets_the_authenticated_identity():
    req = ExportTraceServiceRequest()
    req.resource_spans.append(ResourceSpans(scope_spans=[ScopeSpans(spans=[Span(name="op", trace_id=b"\x01" * 16)])]))
    got = attributes(decode(rewrite_request(req.SerializeToString(), OWNED)))
    assert got == OWNED


def test_malformed_bytes_are_refused_not_forwarded():
    with pytest.raises(WireError):
        rewrite_request(b"\xff\xff\xff", OWNED)                     # truncated varint
    with pytest.raises(WireError):
        rewrite_request(b"\x0a\x20abc", OWNED)                       # length runs past the end
    with pytest.raises(WireError):
        rewrite_request(b"\x0b", OWNED)                              # wire type 3 (group): not permitted
