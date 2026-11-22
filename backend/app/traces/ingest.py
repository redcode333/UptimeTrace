"""Trusted OTLP trace ingestion. Authenticates the sender, binds it to ONE registered service, and forwards privately.

Trust boundary (in order):
  1. The sender presents a bearer token. Only its SHA-256 is stored (credential file, outside the repository).
  2. The token maps to exactly one (service_id, workspace_id). Both come from the credential, never from the request body.
  3. The service must still be registered in that workspace (revocation: remove the service or the credential).
  4. Every resource's identity attributes (service.name, uptimetrace.*) are replaced with that identity. The sender's values
     are discarded, so a credential for service A cannot emit spans attributed to service B.
  5. The rewritten request goes to the Collector with the backend's own bearer token. The Collector refuses anything else.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import secrets
import sys
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx
from fastapi import APIRouter, Request, Response
from fastapi.concurrency import run_in_threadpool
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app import errors
from app.ingest.request import read_body_limited
from app.models import Service
from app.traces.jaeger import TraceBackendError
from app.traces.otlp import WireError, rewrite_request

PROTOBUF = "application/x-protobuf"
MAX_BODY_BYTES = 4 * 1024 * 1024
SHA256 = re.compile(r"^[0-9a-f]{64}$")


@dataclass(frozen=True)
class IngestCredential:
    name: str
    token_sha256: str
    service_id: uuid.UUID
    workspace_id: uuid.UUID


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def parse_credentials(document: Any) -> list[IngestCredential]:
    if not isinstance(document, dict) or not isinstance(document.get("credentials"), list):
        raise ValueError("credential file must be an object with a 'credentials' list")
    out: list[IngestCredential] = []
    names: set[str] = set()
    digests: set[str] = set()
    for i, e in enumerate(document["credentials"]):
        if not isinstance(e, dict):
            raise ValueError(f"credentials[{i}] must be an object")
        name, digest = e.get("name"), e.get("token_sha256")
        if not isinstance(name, str) or not name or len(name) > 64:
            raise ValueError(f"credentials[{i}].name is required (1..64 characters)")
        if not isinstance(digest, str) or not SHA256.match(digest):
            raise ValueError(f"credentials[{i}].token_sha256 must be 64 lowercase hex characters")
        if name in names or digest in digests:
            raise ValueError(f"credentials[{i}] duplicates another credential")
        names.add(name)
        digests.add(digest)
        try:
            service_id = uuid.UUID(str(e.get("service_id")))
            workspace_id = uuid.UUID(str(e.get("workspace_id")))
        except ValueError as exc:
            raise ValueError(f"credentials[{i}] needs service_id and workspace_id UUIDs") from exc
        out.append(IngestCredential(name, digest, service_id, workspace_id))
    return out


class CredentialStore:
    """Reads the credential file, and re-reads it when it changes, so a revoked credential stops working without a restart."""

    def __init__(self, path: Path):
        self._path = path
        self._mtime: float | None = None
        self._creds: list[IngestCredential] = []

    def current(self) -> list[IngestCredential]:
        try:
            mtime = self._path.stat().st_mtime
        except OSError:
            return []                                    # no file: nothing is authorised (fail closed)
        if mtime != self._mtime:
            try:
                self._creds = parse_credentials(json.loads(self._path.read_text(encoding="utf-8")))
            except (OSError, ValueError):
                self._creds = []                         # an unreadable file authorises nothing
            self._mtime = mtime
        return self._creds


def authenticate(creds: list[IngestCredential], authorization: str | None) -> IngestCredential | None:
    if not authorization or not authorization.startswith("Bearer "):
        return None
    token = authorization[len("Bearer "):].strip()
    if not token:
        return None
    digest = hash_token(token)
    match: IngestCredential | None = None
    for c in creds:                                      # every credential is compared: no early exit on a hit
        if hmac.compare_digest(c.token_sha256.encode(), digest.encode()):
            match = c
    return match


def make_collector_forwarder(url: str, token: str, timeout: float = 5.0,
                             transport: httpx.BaseTransport | None = None) -> Callable[[bytes], tuple[int, bytes]]:
    client = httpx.Client(base_url=url.rstrip("/"), timeout=timeout, transport=transport, follow_redirects=False)

    def forward(body: bytes) -> tuple[int, bytes]:
        try:
            r = client.post("/v1/traces", content=body,
                            headers={"Authorization": f"Bearer {token}", "Content-Type": PROTOBUF})
        except httpx.HTTPError as exc:
            raise TraceBackendError("collector unreachable") from exc
        if r.status_code >= 500 or r.status_code in (401, 403):
            raise TraceBackendError(f"collector answered {r.status_code}")
        return r.status_code, r.content
    return forward


class TraceIngest:
    def __init__(self, session_factory: sessionmaker[Session], store: CredentialStore,
                 forward: Callable[[bytes], tuple[int, bytes]], max_bytes: int = MAX_BODY_BYTES):
        self.session_factory = session_factory
        self.store = store
        self.forward = forward
        self.max_bytes = max_bytes


def _registered_name(session_factory: sessionmaker[Session], cred: IngestCredential) -> str | None:
    with session_factory() as session:
        return session.execute(select(Service.name).where(Service.id == cred.service_id,
                                                          Service.workspace_id == cred.workspace_id)).scalar_one_or_none()


def build_otlp_router(ingest: TraceIngest) -> APIRouter:
    router = APIRouter()

    @router.post("/otlp/v1/traces", status_code=200)
    async def export_traces(request: Request):
        cred = await run_in_threadpool(authenticate, ingest.store.current(), request.headers.get("authorization"))
        if cred is None:
            raise errors.unauthorized()
        if (request.headers.get("content-type") or "").split(";")[0].strip().lower() != PROTOBUF:
            raise errors.ApiError(415, "unsupported_media_type", f"Content-Type must be {PROTOBUF}.")
        body = await read_body_limited(request, ingest.max_bytes)
        name = await run_in_threadpool(_registered_name, ingest.session_factory, cred)
        if name is None:
            raise errors.ApiError(403, "service_not_registered", "The credential's service is not registered in its workspace.")
        try:
            rewritten = rewrite_request(body, {"service.name": name, "uptimetrace.service.id": str(cred.service_id),
                                               "uptimetrace.workspace.id": str(cred.workspace_id)})
        except WireError:
            raise errors.ApiError(400, "invalid_otlp", "The body is not a valid OTLP trace export request.") from None
        try:
            status, payload = await run_in_threadpool(ingest.forward, rewritten)
        except TraceBackendError:
            raise errors.ApiError(503, "trace_ingest_unavailable", "Traces could not be accepted right now. Retry later.") from None
        return Response(content=payload, status_code=status, media_type=PROTOBUF)

    return router


def _issue(argv: list[str]) -> int:
    """Create a credential for one service. The token is printed once; only its SHA-256 is written."""
    import argparse
    ap = argparse.ArgumentParser(prog="python -m app.traces.ingest issue")
    ap.add_argument("--name", required=True)
    ap.add_argument("--service-id", required=True, type=uuid.UUID)
    ap.add_argument("--workspace-id", required=True, type=uuid.UUID)
    ap.add_argument("--file", required=True, type=Path)
    a = ap.parse_args(argv)
    doc: dict[str, Any] = {"credentials": []}
    if a.file.exists():
        doc = json.loads(a.file.read_text(encoding="utf-8"))
    if any(isinstance(e, dict) and e.get("name") == a.name for e in doc.get("credentials", [])):
        print(f"a credential named {a.name!r} already exists", file=sys.stderr)
        return 1
    token = secrets.token_urlsafe(32)
    doc.setdefault("credentials", []).append({"name": a.name, "token_sha256": hash_token(token),
                                              "service_id": str(a.service_id), "workspace_id": str(a.workspace_id)})
    parse_credentials(doc)                               # refuse to write a file the server would not accept
    tmp = a.file.with_suffix(a.file.suffix + ".tmp")
    tmp.write_text(json.dumps(doc, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, a.file)
    print(token)
    return 0


if __name__ == "__main__":
    if len(sys.argv) >= 2 and sys.argv[1] == "issue":
        sys.exit(_issue(sys.argv[2:]))
    print("usage: python -m app.traces.ingest issue --name N --service-id UUID --workspace-id UUID --file PATH", file=sys.stderr)
    sys.exit(2)
