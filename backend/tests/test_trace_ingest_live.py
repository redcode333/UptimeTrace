"""End to end through the REAL Collector and Jaeger (opt-in). Start the stack first (demo/tracing) with the same token, then:

    TRACE_COLLECTOR_TOKEN=... UPTIMETRACE_LIVE_COLLECTOR_URL=http://127.0.0.1:4318 UPTIMETRACE_LIVE_JAEGER_URL=http://127.0.0.1:16686
    python -m pytest tests/test_trace_ingest_live.py

The Collector's own authentication is tested directly, then spans go through the trusted ingestion route to the Collector, and
what Jaeger stored is checked.
"""
from __future__ import annotations

import os
import time
import uuid

import httpx
import pytest
from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import ExportTraceServiceRequest

from app.db import make_session_factory
from app.main import create_app
from app.registry.store import RegistryCredentialStore
from app.traces import summary as S
from app.traces.ingest import CredentialStore, TraceIngest, make_collector_forwarder
from app.traces.jaeger import JaegerClient
from tests.clerk_support import verifier
from tests.test_alerts import Clock, T0
from tests.test_trace_ingest import SERVICE_A, SERVICE_B, TOKEN_A, TOKEN_B, cred_file, seeded_services, spans_claiming, sent_as  # noqa: F401

pytestmark = pytest.mark.anyio

COLLECTOR = os.environ.get("UPTIMETRACE_LIVE_COLLECTOR_URL")
JAEGER = os.environ.get("UPTIMETRACE_LIVE_JAEGER_URL")
COLLECTOR_TOKEN = os.environ.get("TRACE_COLLECTOR_TOKEN")
requires_stack = pytest.mark.skipif(not (COLLECTOR and JAEGER and COLLECTOR_TOKEN),
                                    reason="set UPTIMETRACE_LIVE_COLLECTOR_URL, UPTIMETRACE_LIVE_JAEGER_URL and TRACE_COLLECTOR_TOKEN")


@requires_stack
def test_the_collector_itself_refuses_unauthenticated_and_wrong_token_requests():
    empty = ExportTraceServiceRequest().SerializeToString()
    headers = {"Content-Type": "application/x-protobuf"}
    with httpx.Client(timeout=10) as c:
        assert c.post(f"{COLLECTOR}/v1/traces", content=empty, headers=headers).status_code == 401
        assert c.post(f"{COLLECTOR}/v1/traces", content=empty, headers={**headers, "Authorization": "Bearer not-the-token"}).status_code == 401
        assert c.post(f"{COLLECTOR}/v1/traces", content=empty, headers={**headers, "Authorization": f"Bearer {COLLECTOR_TOKEN}"}).status_code == 200


@requires_stack
async def test_spans_from_service_a_reach_jaeger_attributed_only_to_service_a(app_engine, cred_file, seeded_services):  # noqa: F811
    marker = "live-" + uuid.uuid4().hex[:10]
    forward = make_collector_forwarder(COLLECTOR, COLLECTOR_TOKEN)
    sf = make_session_factory(app_engine)
    app = create_app(app_engine, RegistryCredentialStore(sf), clock=Clock(T0), verifier=verifier(),
                     trace_ingest=TraceIngest(sf, CredentialStore(cred_file), forward))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app, raise_app_exceptions=False), base_url="http://test") as c:
        # Service A's credential, claiming to be service B (and another workspace). Must be normalised to A.
        headers, body = sent_as(TOKEN_A, spans_claiming("payments", str(SERVICE_B), workspace="WS-OTHER", operation=marker))
        r = await c.post("/otlp/v1/traces", content=body, headers=headers)
        assert r.status_code == 200, r.text
        # Service B's own credential, normal path.
        headers, body = sent_as(TOKEN_B, spans_claiming("payments", str(SERVICE_B), operation=marker + "-b"))
        r = await c.post("/otlp/v1/traces", content=body, headers=headers)
        assert r.status_code == 200, r.text

    client = JaegerClient(JAEGER)
    deadline = time.time() + 40
    found_a = []
    while time.time() < deadline and not found_a:
        time.sleep(2)
        found_a = [t for t in client.search("checkout", 0, 2 ** 62, 50, tags={S.SERVICE_ID_TAG: str(SERVICE_A)})
                   if any(sp["operationName"] == marker for sp in t["spans"])]
    assert found_a, "service A's span did not reach Jaeger under service A"
    span_services = {S.process(t, sp)[0] for t in found_a for sp in t["spans"] if sp["operationName"] == marker}
    assert span_services == {"checkout"}
    # Nothing from service A's credential is attributed to service B, under its name or its identity.
    assert not any(any(sp["operationName"] == marker for sp in t["spans"])
                   for t in client.search("payments", 0, 2 ** 62, 50, tags={S.SERVICE_ID_TAG: str(SERVICE_B)}))
