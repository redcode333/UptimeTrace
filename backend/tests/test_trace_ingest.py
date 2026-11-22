"""Trusted OTLP ingestion through the real app and database: who may send, what identity their spans carry, and what happens
when the Collector is unavailable. The Collector itself is replaced by a recorder here; test_trace_ingest_live.py runs the
real Collector.
"""
from __future__ import annotations

import hashlib
import json
import os
import uuid

import httpx
import pytest
from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import ExportTraceServiceRequest
from opentelemetry.proto.common.v1.common_pb2 import AnyValue, KeyValue
from opentelemetry.proto.resource.v1.resource_pb2 import Resource
from opentelemetry.proto.trace.v1.trace_pb2 import ResourceSpans, ScopeSpans, Span
from sqlalchemy import text

from app.db import make_session_factory
from app.main import create_app
from app.registry.store import RegistryCredentialStore
from app.traces.ingest import CredentialStore, TraceIngest
from app.traces.jaeger import TraceBackendError
from tests.test_alerts import Clock, T0
from tests.clerk_support import verifier

pytestmark = pytest.mark.anyio

SERVICE_A = uuid.UUID("aaaaaaaa-0000-4000-8000-00000000000a")
SERVICE_B = uuid.UUID("bbbbbbbb-0000-4000-8000-00000000000b")
TOKEN_A = "token-for-service-a-only-0123456789"
TOKEN_B = "token-for-service-b-only-0123456789"


@pytest.fixture(autouse=True)
def seeded_services(engine):
    """Service A in the default workspace, service B in another workspace. Removed after each test."""
    def clear():
        with engine.begin() as c:
            c.execute(text("DELETE FROM service_hosts"))
            c.execute(text("DELETE FROM services"))
            c.execute(text("DELETE FROM workspaces WHERE slug = 'other-tenant'"))
    clear()
    with engine.begin() as c:
        ws = c.execute(text("SELECT id FROM workspaces WHERE slug = 'default'")).scalar_one()
        other = c.execute(text("INSERT INTO workspaces (slug, name) VALUES ('other-tenant', 'Other') RETURNING id")).scalar_one()
        c.execute(text("INSERT INTO services (id, workspace_id, name, created_by) VALUES (:i, :w, 'checkout', 'test')"),
                  {"i": SERVICE_A, "w": ws})
        c.execute(text("INSERT INTO services (id, workspace_id, name, created_by) VALUES (:i, :w, 'payments', 'test')"),
                  {"i": SERVICE_B, "w": other})
    yield
    clear()


def write_credentials(path, entries):
    doc = {"credentials": [{"name": n, "token_sha256": hashlib.sha256(t.encode()).hexdigest(),
                            "service_id": str(s), "workspace_id": str(w)} for n, t, s, w in entries]}
    path.write_text(json.dumps(doc), encoding="utf-8")


@pytest.fixture
def cred_file(tmp_path, engine):
    path = tmp_path / "trace-ingest-credentials.json"
    with engine.connect() as c:
        ws = c.execute(text("SELECT id FROM workspaces WHERE slug = 'default'")).scalar_one()
        other = c.execute(text("SELECT id FROM workspaces WHERE slug = 'other-tenant'")).scalar_one()
    write_credentials(path, [("svc-a", TOKEN_A, SERVICE_A, ws), ("svc-b", TOKEN_B, SERVICE_B, other)])
    return path


class Recorder:
    """Stands in for the Collector: records what would have been forwarded, or fails on demand."""

    def __init__(self, fail: bool = False):
        self.calls: list[bytes] = []
        self.fail = fail

    def __call__(self, body: bytes):
        if self.fail:
            raise TraceBackendError("collector unreachable")
        self.calls.append(body)
        return 200, b""


def make_app(app_engine, cred_path, recorder, max_bytes=4 * 1024 * 1024):
    clock = Clock(T0)
    sf = make_session_factory(app_engine)
    ingest = TraceIngest(sf, CredentialStore(cred_path), recorder, max_bytes=max_bytes)
    app = create_app(app_engine, RegistryCredentialStore(sf), clock=clock, verifier=verifier(), trace_ingest=ingest)
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app, raise_app_exceptions=False), base_url="http://test")


def spans_claiming(service_name: str, service_id: str, workspace: str = "WS", operation: str = "GET /checkout") -> bytes:
    req = ExportTraceServiceRequest()
    rs = req.resource_spans.add()
    rs.resource.CopyFrom(Resource(attributes=[
        KeyValue(key="service.name", value=AnyValue(string_value=service_name)),
        KeyValue(key="uptimetrace.service.id", value=AnyValue(string_value=service_id)),
        KeyValue(key="uptimetrace.workspace.id", value=AnyValue(string_value=workspace)),
    ]))
    span = rs.scope_spans.add().spans.add()
    span.trace_id, span.span_id, span.name = os.urandom(16), os.urandom(8), operation
    return req.SerializeToString()


def sent_as(token: str | None, body: bytes, content_type: str = "application/x-protobuf"):
    headers = {"Content-Type": content_type}
    if token is not None:
        headers["Authorization"] = f"Bearer {token}"
    return headers, body


def decode_identity(body: bytes) -> dict[str, str]:
    req = ExportTraceServiceRequest()
    req.ParseFromString(body)
    return {a.key: a.value.string_value for a in req.resource_spans[0].resource.attributes}


# ------------------------------------------------------------------------------------------------ who may send
async def test_no_credential_is_rejected_and_nothing_is_forwarded(app_engine, cred_file):
    rec = Recorder()
    async with make_app(app_engine, cred_file, rec) as c:
        headers, body = sent_as(None, spans_claiming("checkout", str(SERVICE_A)))
        r = await c.post("/otlp/v1/traces", content=body, headers=headers)
    assert r.status_code == 401 and rec.calls == []


@pytest.mark.parametrize("authorization", ["Bearer wrong-token-value", "Basic dXNlcjpwYXNz", "Bearer ", "Token token-for-service-a-only-0123456789"])
async def test_invalid_or_malformed_credentials_are_rejected(app_engine, cred_file, authorization):
    rec = Recorder()
    async with make_app(app_engine, cred_file, rec) as c:
        r = await c.post("/otlp/v1/traces", content=spans_claiming("checkout", str(SERVICE_A)),
                         headers={"Content-Type": "application/x-protobuf", "Authorization": authorization})
    assert r.status_code == 401 and rec.calls == []


async def test_a_missing_credential_file_authorises_nothing(app_engine, tmp_path):
    rec = Recorder()
    async with make_app(app_engine, tmp_path / "does-not-exist.json", rec) as c:
        headers, body = sent_as(TOKEN_A, spans_claiming("checkout", str(SERVICE_A)))
        r = await c.post("/otlp/v1/traces", content=body, headers=headers)
    assert r.status_code == 401 and rec.calls == []


async def test_a_revoked_credential_stops_working_without_a_restart(app_engine, cred_file, engine):
    rec = Recorder()
    async with make_app(app_engine, cred_file, rec) as c:
        headers, body = sent_as(TOKEN_A, spans_claiming("checkout", str(SERVICE_A)))
        assert (await c.post("/otlp/v1/traces", content=body, headers=headers)).status_code == 200
        write_credentials(cred_file, [("svc-b", TOKEN_B, SERVICE_B, uuid.uuid4())])   # service A's credential removed
        os.utime(cred_file, (os.path.getatime(cred_file), os.path.getmtime(cred_file) + 5))
        assert (await c.post("/otlp/v1/traces", content=body, headers=headers)).status_code == 401


async def test_a_removed_service_can_no_longer_send(app_engine, cred_file, engine):
    rec = Recorder()
    async with make_app(app_engine, cred_file, rec) as c:
        with engine.begin() as cn:
            cn.execute(text("DELETE FROM services WHERE id = :i"), {"i": SERVICE_A})
        headers, body = sent_as(TOKEN_A, spans_claiming("checkout", str(SERVICE_A)))
        r = await c.post("/otlp/v1/traces", content=body, headers=headers)
    assert r.status_code == 403 and r.json()["error"]["code"] == "service_not_registered" and rec.calls == []


# ------------------------------------------------------------------------------------------------ what identity is recorded
async def test_service_a_credential_claiming_service_b_is_normalised_to_service_a(app_engine, cred_file):
    rec = Recorder()
    async with make_app(app_engine, cred_file, rec) as c:
        headers, body = sent_as(TOKEN_A, spans_claiming("payments", str(SERVICE_B), workspace="WS-OTHER"))
        r = await c.post("/otlp/v1/traces", content=body, headers=headers)
    assert r.status_code == 200 and len(rec.calls) == 1
    identity = decode_identity(rec.calls[0])
    assert identity["uptimetrace.service.id"] == str(SERVICE_A)
    assert identity["service.name"] == "checkout"
    assert identity["uptimetrace.workspace.id"] != "WS-OTHER"


async def test_service_b_credential_is_bound_to_service_b_and_its_workspace(app_engine, cred_file, engine):
    rec = Recorder()
    async with make_app(app_engine, cred_file, rec) as c:
        headers, body = sent_as(TOKEN_B, spans_claiming("checkout", str(SERVICE_A)))
        assert (await c.post("/otlp/v1/traces", content=body, headers=headers)).status_code == 200
    identity = decode_identity(rec.calls[0])
    with engine.connect() as cn:
        other = cn.execute(text("SELECT id FROM workspaces WHERE slug = 'other-tenant'")).scalar_one()
    assert identity == {"service.name": "payments", "uptimetrace.service.id": str(SERVICE_B), "uptimetrace.workspace.id": str(other)}


# ------------------------------------------------------------------------------------------------ input and failure handling
async def test_a_non_protobuf_body_is_refused(app_engine, cred_file):
    rec = Recorder()
    async with make_app(app_engine, cred_file, rec) as c:
        headers, body = sent_as(TOKEN_A, b"{}", content_type="application/json")
        assert (await c.post("/otlp/v1/traces", content=body, headers=headers)).status_code == 415
    assert rec.calls == []


async def test_a_malformed_protobuf_body_is_refused_not_forwarded(app_engine, cred_file):
    rec = Recorder()
    async with make_app(app_engine, cred_file, rec) as c:
        headers, body = sent_as(TOKEN_A, b"\xff\xff\xff\x01")
        assert (await c.post("/otlp/v1/traces", content=body, headers=headers)).status_code == 400
    assert rec.calls == []


async def test_an_oversized_body_is_refused(app_engine, cred_file):
    rec = Recorder()
    async with make_app(app_engine, cred_file, rec, max_bytes=512) as c:
        headers, body = sent_as(TOKEN_A, spans_claiming("checkout", str(SERVICE_A)) + b"\x00" * 2048)
        assert (await c.post("/otlp/v1/traces", content=body, headers=headers)).status_code == 413
    assert rec.calls == []


async def test_collector_unavailable_is_a_retryable_failure_for_the_sender(app_engine, cred_file):
    async with make_app(app_engine, cred_file, Recorder(fail=True)) as c:
        headers, body = sent_as(TOKEN_A, spans_claiming("checkout", str(SERVICE_A)))
        r = await c.post("/otlp/v1/traces", content=body, headers=headers)
    assert r.status_code == 503 and r.json()["error"]["code"] == "trace_ingest_unavailable"


async def test_ingestion_is_off_unless_configured(app_engine):
    clock = Clock(T0)
    app = create_app(app_engine, RegistryCredentialStore(make_session_factory(app_engine)), clock=clock, verifier=verifier())
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app, raise_app_exceptions=False), base_url="http://test") as c:
        r = await c.post("/otlp/v1/traces", content=b"", headers={"Content-Type": "application/x-protobuf"})
    assert r.status_code == 404
