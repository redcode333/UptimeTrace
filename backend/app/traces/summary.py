"""Pure functions over Jaeger trace documents: ownership, summaries and span lists. No I/O, no database.

Ownership: a span's process carries the resource attribute `uptimetrace.service.id`, set by the instrumented application to
the UUID of its registered UptimeTrace service. A trace is attributed to a service only through that attribute, and the
caller only ever sees services of their own workspace (see service.py). The `service.name` attribute is a label, not identity.
"""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

SERVICE_ID_TAG = "uptimetrace.service.id"
SLOW_SPAN_MS = 1000.0
MAX_SPANS = 500


def _tag(tags: list[dict[str, Any]] | None, key: str) -> Any:
    for t in tags or []:
        if t.get("key") == key:
            return t.get("value")
    return None


def _ms(micros: int | float) -> float:
    return round(micros / 1000.0, 1)


def _iso(micros: int) -> str:
    return datetime.fromtimestamp(micros / 1_000_000, UTC).isoformat().replace("+00:00", "Z")


def process(trace: dict[str, Any], span: dict[str, Any]) -> tuple[str, str | None]:
    p = trace.get("processes", {}).get(span.get("processID"), {})
    return p.get("serviceName") or "unknown", _tag(p.get("tags"), SERVICE_ID_TAG)


def service_ids(trace: dict[str, Any]) -> set[str]:
    return {str(v) for p in trace.get("processes", {}).values() if (v := _tag(p.get("tags"), SERVICE_ID_TAG)) is not None}


def belongs_to(trace: dict[str, Any], allowed_service_ids: set[str]) -> bool:
    return bool(service_ids(trace) & allowed_service_ids)


def is_error(span: dict[str, Any]) -> bool:
    tags = span.get("tags")
    return _tag(tags, "error") is True or str(_tag(tags, "otel.status_code") or "").upper() == "ERROR"


def _parent_ids(span: dict[str, Any]) -> set[str]:
    return {r.get("spanID") for r in span.get("references", []) if r.get("refType") == "CHILD_OF"}


def _root(trace: dict[str, Any]) -> dict[str, Any]:
    spans = trace["spans"]
    ids = {s["spanID"] for s in spans}
    roots = [s for s in spans if not (_parent_ids(s) & ids)] or spans
    return min(roots, key=lambda s: s["startTime"])


def summarize(trace: dict[str, Any]) -> dict[str, Any]:
    spans = trace.get("spans") or []
    if not spans:
        raise ValueError("trace has no spans")
    start = min(s["startTime"] for s in spans)
    end = max(s["startTime"] + s["duration"] for s in spans)
    root = _root(trace)
    root_service, _ = process(trace, root)
    others = [s for s in spans if s is not root] or [root]
    slowest = max(others, key=lambda s: s["duration"])
    errors = [s for s in spans if is_error(s)]
    services = sorted({process(trace, s)[0] for s in spans})
    return {
        "trace_id": trace["traceID"],
        "started_at": _iso(start),
        "duration_ms": _ms(end - start),
        "root": {"service": root_service, "operation": root["operationName"]},
        "services": services,
        "span_count": len(spans),
        "error_count": len(errors),
        "slow_span_count": sum(1 for s in spans if s["duration"] / 1000.0 >= SLOW_SPAN_MS),
        "slowest": {"service": process(trace, slowest)[0], "operation": slowest["operationName"],
                    "duration_ms": _ms(slowest["duration"])},
    }


def span_list(trace: dict[str, Any]) -> dict[str, Any]:
    spans = sorted(trace.get("spans") or [], key=lambda s: (s["startTime"], s["spanID"]))
    if not spans:
        return {"spans": [], "truncated": False}
    origin = min(s["startTime"] for s in spans)
    out = []
    for s in spans[:MAX_SPANS]:
        parents = _parent_ids(s)
        out.append({
            "span_id": s["spanID"],
            "parent_span_id": sorted(parents)[0] if parents else None,
            "service": process(trace, s)[0],
            "operation": s["operationName"],
            "offset_ms": _ms(s["startTime"] - origin),
            "duration_ms": _ms(s["duration"]),
            "error": is_error(s),
            "slow": s["duration"] / 1000.0 >= SLOW_SPAN_MS,
        })
    return {"spans": out, "truncated": len(spans) > MAX_SPANS}
