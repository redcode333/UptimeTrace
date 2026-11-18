from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Callable

from fastapi import Depends, FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.exceptions import RequestValidationError
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy import Engine, text
from sqlalchemy.exc import DBAPIError, SQLAlchemyError
from sqlalchemy.exc import TimeoutError as PoolTimeoutError
from starlette.exceptions import HTTPException as StarletteHTTPException

from app import errors
from app.auth import CredentialStore
from app.clerk_auth import ClerkVerifier
from app.user_auth import build_me_router, make_dependencies
from app.config import MAX_BODY_BYTES, SCHEMA_PATH, SUPPORTED_SCHEMA_VERSIONS
from app.db import make_session_factory
from app.errors import ApiError, ErrorDetail
from app.monitoring.router import build_router
from app.registry.router import build_registry_router
from app.mcp.router import build_mcp_token_router
from app.ingest import request as req
from app.ingest.fingerprint import content_hash
from app.ingest.schema import EnvelopeSchemaValidator
from app.ingest.semantic import validate_semantics
from app.ingest.service import EnvelopeConflict, store_envelope
from app.ingest.storage_limits import find_nul_characters

log = logging.getLogger("uptimetrace")


def is_database_unavailable(exc: SQLAlchemyError) -> bool:
    return isinstance(exc, (DBAPIError, PoolTimeoutError))


class AcceptedResponse(BaseModel):
    """Typed response body only; carries no validation rules of its own."""

    status: str = "accepted"
    envelope_id: str
    accepted_metrics: int


def create_app(
    engine: Engine,
    credentials: CredentialStore,
    clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    schema_path: Path = SCHEMA_PATH,
    max_body_bytes: int = MAX_BODY_BYTES,
    verifier: ClerkVerifier | None = None,
    mcp_server=None,
) -> FastAPI:
    """`mcp_server` (a FastMCP built by app.mcp.server) is OFF unless passed: the MCP endpoint is opt-in per deployment."""
    lifespan = None
    mcp_asgi = None
    if mcp_server is not None:
        mcp_asgi = mcp_server.streamable_http_app()          # creates the session manager

        @asynccontextmanager
        async def lifespan(_: FastAPI):
            async with mcp_server.session_manager.run():
                yield

    app = FastAPI(title="UptimeTrace ingestion", docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)
    session_factory = make_session_factory(engine)
    schema_validator = EnvelopeSchemaValidator(schema_path)

    @app.exception_handler(ApiError)
    async def _api_error(_: Request, exc: ApiError):
        return JSONResponse(exc.body(), status_code=exc.status, headers=exc.headers)

    @app.exception_handler(RequestValidationError)
    async def _bad_params(_: Request, exc: RequestValidationError):
        # Read-API query/path parameters. Fixed wording per field; the offending input value is never echoed.
        details = [ErrorDetail(".".join(str(p) for p in e["loc"][1:]) or "request", e["msg"].split(",")[0][:200])
                   for e in exc.errors()[:20]]
        err = errors.invalid_request(details)
        return JSONResponse(err.body(), status_code=err.status)

    @app.exception_handler(SQLAlchemyError)
    async def _db_error(_: Request, exc: SQLAlchemyError):
        # Log the exception class only: SQLAlchemy messages embed SQL text and parameters.
        log.error("database error during request: %s", type(exc).__name__)
        # Database-side failures (connection refused/lost, timeouts, pool exhaustion, a failed
        # commit) mean "not durably accepted, retry" -> 503. SQLAlchemy errors that are not
        # database failures (misuse of the ORM/engine) are application bugs -> 500.
        err = errors.unavailable() if is_database_unavailable(exc) else errors.internal_error()
        return JSONResponse(err.body(), status_code=err.status)

    @app.exception_handler(StarletteHTTPException)
    async def _http_error(_: Request, exc: StarletteHTTPException):
        # Routing-level errors (unknown path, wrong method). Fixed messages: never echo exc.detail.
        err = errors.http_error(exc.status_code)
        headers = dict(exc.headers) if exc.headers else None  # keeps `Allow` on 405
        return JSONResponse(err.body(), status_code=err.status, headers=headers)

    @app.exception_handler(Exception)
    async def _unhandled(_: Request, exc: Exception):
        log.error("unhandled error: %s", type(exc).__name__)
        err = errors.internal_error()
        return JSONResponse(err.body(), status_code=err.status)

    @app.get("/health")
    def health():
        database = "ok"
        try:
            with engine.connect() as connection:
                connection.execute(text("SELECT 1"))
        except Exception as exc:  # any failure means "not reachable"; never leak details
            log.warning("health check: database unavailable (%s)", type(exc).__name__)
            database = "unavailable"
        healthy = database == "ok"
        return JSONResponse(
            {"status": "ok" if healthy else "degraded", "app": "ok", "database": database},
            status_code=200 if healthy else 503,
        )

    @app.post("/api/v1/telemetry", status_code=202)
    async def ingest(request: Request):
        # Authentication comes first: an unauthenticated caller costs us no body read, parse or validation.
        # The store may consult the registry database, so it runs off the event loop; a DB failure is a 503 (fail closed).
        credential = await run_in_threadpool(credentials.authenticate, request.headers.get("authorization"))
        if credential is None:
            raise errors.unauthorized()
        # Then the contract order: size -> content type -> JSON parse -> schema_version -> schema -> semantics.
        body = await req.read_body_limited(request, max_body_bytes)
        req.check_content_type(request.headers.get("content-type"))
        document = req.parse_json_object(body)
        req.check_schema_version(document, SUPPORTED_SCHEMA_VERSIONS)

        problems = schema_validator.validate(document)
        if problems:
            raise errors.validation_error(problems)
        # Host identity validation (the fields now exist and are well-formed): the token is bound to this agent and host(s).
        if not credentials.permits(credential, document):
            raise errors.forbidden()
        problems = [*validate_semantics(document, clock()), *find_nul_characters(document)]
        if problems:
            raise errors.validation_error(problems)

        digest = content_hash(document)
        try:
            # Blocking DB work runs in a worker thread so concurrent requests really overlap.
            await run_in_threadpool(store_envelope, session_factory, document, digest)
        except EnvelopeConflict:
            raise errors.envelope_id_conflict(document["envelope_id"]) from None

        # Reached only after store_envelope returned, i.e. after COMMIT.
        accepted = AcceptedResponse(
            envelope_id=document["envelope_id"], accepted_metrics=len(document["metrics"])
        )
        return JSONResponse(accepted.model_dump(), status_code=202)

    authenticate, authorize = make_dependencies(verifier)
    # Browser API: every monitoring route needs a verified Clerk session AND an allowlisted user. /health and agent ingestion are separate.
    app.include_router(build_router(session_factory, clock, dependencies=[Depends(authorize)]))
    app.include_router(build_registry_router(session_factory, clock, authorize))
    app.include_router(build_mcp_token_router(session_factory, clock, authorize))
    if mcp_asgi is not None:
        app.mount("/mcp", mcp_asgi)                          # clients connect to <base>/mcp/ (bearer token required by the SDK middleware)
    app.include_router(build_me_router(verifier, authenticate))
    if verifier is not None:  # CORS only for the explicitly authorized browser origins, GET/POST/DELETE with a bearer header only, no cookies
        app.add_middleware(CORSMiddleware, allow_origins=list(verifier.config.authorized_parties),
                           allow_methods=["GET", "POST", "DELETE"], allow_headers=["Authorization", "Content-Type"], allow_credentials=False, max_age=600)
    return app
