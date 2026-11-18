from __future__ import annotations

import copy
import json
import uuid

ENVELOPE_TS = "2026-11-11T11:59:59Z"   # 1 s before the fixed test clock (2026-11-11T12:00:00Z)
OBS_TS = "2026-11-11T11:59:58.500Z"


def make_envelope(**overrides) -> dict:
    envelope = {
        "schema_version": "1.0",
        "envelope_id": str(uuid.uuid4()),
        "agent_id": "agent-test",
        "host_id": "host-test",
        "hostname": "test-host",
        "timestamp": ENVELOPE_TS,
        "service": "checkout-api",
        "metrics": [
            {"name": "system.cpu.utilization", "kind": "gauge", "value": 42.5, "unit": "percent", "timestamp": OBS_TS},
            {"name": "system.disk.utilization", "kind": "gauge", "value": 61.0, "unit": "percent",
             "timestamp": OBS_TS, "labels": {"mount": "/"}},
            {"name": "app.queue.depth", "kind": "gauge", "value": 42, "unit": "count", "timestamp": OBS_TS},
        ],
    }
    envelope.update(copy.deepcopy(overrides))
    return envelope


def raw(document: dict, *, reverse_keys: bool = False) -> bytes:
    """Serialise exactly as given; optionally with every object's keys in reverse order."""
    def reorder(value):
        if isinstance(value, dict):
            return {k: reorder(value[k]) for k in reversed(list(value))}
        if isinstance(value, list):
            return [reorder(v) for v in value]
        return value

    return json.dumps(reorder(document) if reverse_keys else document).encode()


JSON_HEADERS = {"content-type": "application/json"}


async def post(client, body: bytes | dict, headers: dict | None = None):
    content = body if isinstance(body, bytes) else raw(body)
    return await client.post("/api/v1/telemetry", content=content, headers=JSON_HEADERS if headers is None else headers)
