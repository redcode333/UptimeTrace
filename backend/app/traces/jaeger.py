"""Client for Jaeger's query API. Reached only from the backend (the browser never talks to Jaeger).

Every failure is raised as TraceBackendError. Callers degrade to "unavailable"; a trace outage must never fail an investigation.
"""
from __future__ import annotations

import json
from typing import Any

import httpx


class TraceBackendError(Exception):
    """The trace backend is unreachable, or answered with something this client cannot use."""


def _json(response: httpx.Response) -> dict[str, Any]:
    try:
        body = response.json()
    except ValueError as exc:
        raise TraceBackendError("invalid JSON from trace backend") from exc
    if not isinstance(body, dict):
        raise TraceBackendError("unexpected response from trace backend")
    return body


class JaegerClient:
    def __init__(self, base_url: str, timeout: float = 3.0, transport: httpx.BaseTransport | None = None):
        self._client = httpx.Client(base_url=base_url.rstrip("/"), timeout=timeout, transport=transport, follow_redirects=False)

    def search(self, service: str, start_us: int, end_us: int, limit: int,
               tags: dict[str, str] | None = None) -> list[dict[str, Any]]:
        """Traces that contain `service`, started in [start, end] (microseconds since the epoch). `tags` are matched by Jaeger
        BEFORE `limit` applies, so other tenants' traces cannot push the caller's traces out of the result."""
        params: dict[str, Any] = {"service": service, "start": start_us, "end": end_us, "limit": limit}
        if tags:
            params["tags"] = json.dumps(tags, separators=(",", ":"))
        try:
            response = self._client.get("/api/traces", params=params)
        except httpx.HTTPError as exc:
            raise TraceBackendError("trace backend unreachable") from exc
        if response.status_code != 200:
            raise TraceBackendError(f"trace backend answered {response.status_code}")
        data = _json(response).get("data")
        if not isinstance(data, list):
            raise TraceBackendError("unexpected search response from trace backend")
        return data

    def trace(self, trace_id: str) -> dict[str, Any] | None:
        """One trace by id, or None when the backend does not have it."""
        try:
            response = self._client.get(f"/api/traces/{trace_id}")
        except httpx.HTTPError as exc:
            raise TraceBackendError("trace backend unreachable") from exc
        if response.status_code == 404:
            return None
        if response.status_code != 200:
            raise TraceBackendError(f"trace backend answered {response.status_code}")
        body = _json(response)
        data = body.get("data")
        if isinstance(data, list) and data:
            return data[0]
        if body.get("errors") and not data:
            return None
        raise TraceBackendError("unexpected trace response from trace backend")

    def close(self) -> None:
        self._client.close()
