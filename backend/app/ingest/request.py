"""Steps 1-4 of the contract's order of checks: size, content type, JSON parse, schema_version."""
from __future__ import annotations

import json

from starlette.requests import Request

from app import errors


async def read_body_limited(request: Request, limit: int) -> bytes:
    """Read the body, refusing anything over `limit` bytes.

    Content-Length is only a fast path: the stream itself is counted, so chunked uploads and
    lying headers are also capped.
    """
    declared = request.headers.get("content-length")
    if declared is not None and declared.isdigit() and int(declared) > limit:
        raise errors.payload_too_large(limit)
    chunks: list[bytes] = []
    total = 0
    async for chunk in request.stream():
        total += len(chunk)
        if total > limit:
            raise errors.payload_too_large(limit)
        chunks.append(chunk)
    return b"".join(chunks)


def check_content_type(header: str | None) -> None:
    if not header:
        raise errors.unsupported_media_type()
    media_type, *params = (part.strip() for part in header.split(";"))
    if media_type.lower() != "application/json":
        raise errors.unsupported_media_type()
    for param in params:
        name, _, value = param.partition("=")
        if name.strip().lower() == "charset" and value.strip().strip('"').lower() != "utf-8":
            raise errors.unsupported_media_type()


def _reject_constant(name: str):
    raise ValueError(f"non-JSON constant {name}")


def parse_json_object(body: bytes) -> dict:
    """Parse strictly: NaN/Infinity literals are malformed JSON; the top level must be an object."""
    try:
        document = json.loads(body.decode("utf-8"), parse_constant=_reject_constant)
    except (UnicodeDecodeError, ValueError, RecursionError):
        raise errors.malformed_json("Request body is not valid UTF-8 JSON.") from None
    if not isinstance(document, dict):
        raise errors.malformed_json("Top-level JSON value must be an object.")
    return document


def check_schema_version(document: dict, supported: list[str]) -> None:
    """A *string* version we do not support is a 400. Missing / non-string falls through to the
    schema, which reports it as a 422 validation_error (contract)."""
    version = document.get("schema_version")
    if isinstance(version, str) and version not in supported:
        raise errors.unsupported_schema_version(version, supported)
