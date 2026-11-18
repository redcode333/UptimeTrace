"""Semantic checks from the contract README ("Timestamps" and "Observation identity").

Run only on envelopes that already passed the JSON Schema. The three checks are independent;
all failures are reported together. Server time is injected so tests need no real clock.
"""
from __future__ import annotations

from datetime import datetime, timedelta

from app.errors import ErrorDetail

OBSERVATION_SKEW = timedelta(seconds=5)      # contract constant, not configurable
ENVELOPE_FUTURE_LIMIT = timedelta(minutes=5)  # contract constant, not configurable


def parse_timestamp(value: str) -> datetime:
    """Schema-valid timestamps are always UTC 'Z' strings, so this yields an aware UTC datetime."""
    return datetime.fromisoformat(value)


def check_observations_vs_envelope(envelope: dict) -> list[ErrorDetail]:
    """Check 1: observation.timestamp <= envelope.timestamp + 5 s."""
    limit = parse_timestamp(envelope["timestamp"]) + OBSERVATION_SKEW
    out = []
    for i, metric in enumerate(envelope["metrics"]):
        if parse_timestamp(metric["timestamp"]) > limit:
            out.append(ErrorDetail(
                f"metrics[{i}].timestamp",
                f"Observation timestamp {metric['timestamp']} is more than 5 seconds after "
                f"the envelope timestamp {envelope['timestamp']}.",
            ))
    return out


def check_envelope_vs_server(envelope: dict, server_now: datetime) -> list[ErrorDetail]:
    """Check 2: envelope.timestamp <= server_now + 5 min. No lower bound."""
    if parse_timestamp(envelope["timestamp"]) > server_now + ENVELOPE_FUTURE_LIMIT:
        return [ErrorDetail(
            "timestamp",
            f"Envelope timestamp {envelope['timestamp']} is more than 5 minutes ahead of server time.",
        )]
    return []


def check_duplicate_observations(envelope: dict) -> list[ErrorDetail]:
    """Check 3: same name + same labels + same observation instant within one envelope.

    Rejected even when the values are equal. Labels compare as a set of pairs (absent == {}),
    timestamps compare as instants. `path` is the second occurrence.
    """
    seen: set[tuple] = set()
    out = []
    for i, metric in enumerate(envelope["metrics"]):
        key = (
            metric["name"],
            frozenset((metric.get("labels") or {}).items()),
            parse_timestamp(metric["timestamp"]),
        )
        if key in seen:
            out.append(ErrorDetail(
                f"metrics[{i}]",
                f"Duplicate observation: '{metric['name']}' with the same labels and timestamp "
                f"already appears earlier in this envelope.",
            ))
        seen.add(key)
    return out


def validate_semantics(envelope: dict, server_now: datetime) -> list[ErrorDetail]:
    return [
        *check_observations_vs_envelope(envelope),
        *check_envelope_vs_server(envelope, server_now),
        *check_duplicate_observations(envelope),
    ]
