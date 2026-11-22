"""Tracing setup shared by the demo services.

Spans go to the UptimeTrace backend's trusted OTLP endpoint, authenticated with this service's own credential
(UPTIMETRACE_INGEST_TOKEN, issued with `python -m app.traces.ingest issue`). The service does NOT claim an identity: the
backend attaches the registered service identity from the credential, whatever the demo sends. No secrets are recorded.
"""
from __future__ import annotations

import os

from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor


def setup_tracing(service_name: str) -> None:
    endpoint = os.environ.get("UPTIMETRACE_OTLP_ENDPOINT", "http://127.0.0.1:8000/otlp/v1/traces")
    token = os.environ["UPTIMETRACE_INGEST_TOKEN"]          # required: fail at start, not silently unauthenticated
    provider = TracerProvider(resource=Resource.create({"service.name": service_name, "service.version": "0.1.0"}))
    exporter = OTLPSpanExporter(endpoint=endpoint, headers={"Authorization": f"Bearer {token}"})
    provider.add_span_processor(BatchSpanProcessor(exporter))
    trace.set_tracer_provider(provider)
