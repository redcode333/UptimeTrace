"""Content fingerprint for idempotency: SHA-256 over a canonical JSON form.

Equivalence is *parsed-JSON equality* (contract), never byte equality:
  * object key order is irrelevant (keys are sorted);
  * numbers that are equal compare equal: 1, 1.0, 1e0 and -0.0 == 0 hash identically;
  * array order, string content and every timestamp string are significant
    ("...:00Z" and "...:00.0Z" are different documents, as they are different JSON strings).
"""
from __future__ import annotations

import hashlib
import json
from typing import Any


def _normalise(value: Any) -> Any:
    if isinstance(value, bool) or value is None or isinstance(value, (str, int)):
        return value
    if isinstance(value, float):
        # Integral floats collapse to int so 1.0 == 1 (and -0.0 == 0), as JSON number equality does.
        return int(value) if value.is_integer() else value
    if isinstance(value, list):
        return [_normalise(v) for v in value]
    if isinstance(value, dict):
        return {k: _normalise(v) for k, v in value.items()}
    raise TypeError(f"not a JSON value: {type(value).__name__}")


def canonical_json(document: dict) -> str:
    return json.dumps(
        _normalise(document),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )


def content_hash(document: dict) -> str:
    return hashlib.sha256(canonical_json(document).encode("utf-8")).hexdigest()
