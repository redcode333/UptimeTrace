"""Canonical JSON Schema validation. The schema file is the only home of structural rules."""
from __future__ import annotations

import json
from pathlib import Path

from jsonschema import Draft202012Validator

from app.errors import ErrorDetail

MAX_DETAILS = 100
MAX_MESSAGE_CHARS = 300


def format_path(parts) -> str:
    """Deque of JSON path parts -> 'metrics[0].labels.mount' ('$' for the document root)."""
    out = ""
    for part in parts:
        out += f"[{part}]" if isinstance(part, int) else f".{part}"
    return out.lstrip(".") or "$"


class EnvelopeSchemaValidator:
    def __init__(self, schema_path: Path):
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
        Draft202012Validator.check_schema(schema)
        # No format_checker on purpose: the schema is self-contained (see contract README).
        self._validator = Draft202012Validator(schema)

    def validate(self, document: dict) -> list[ErrorDetail]:
        seen: set[tuple[str, str]] = set()
        details: list[ErrorDetail] = []
        for error in sorted(self._validator.iter_errors(document), key=lambda e: (list(map(str, e.absolute_path)), e.message)):
            message = error.message
            if len(message) > MAX_MESSAGE_CHARS:
                message = message[:MAX_MESSAGE_CHARS] + "..."
            key = (format_path(error.absolute_path), message)
            if key in seen:
                continue
            seen.add(key)
            details.append(ErrorDetail(*key))
            if len(details) >= MAX_DETAILS:
                break
        return details
