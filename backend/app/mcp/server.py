"""The MCP server (official `mcp` SDK, FastMCP, Streamable HTTP). It is an INTERFACE onto the existing services.

Tools (read-only, first release) - model-controlled actions that fetch bounded, workspace-scoped data:
    list_hosts, get_host_health, get_host_metrics.
Resources and prompts are deliberately NOT registered: tools cover every use case today, resources would add an
unbounded-read surface, and prompts would only restate documentation (see docs/MCP_INTEGRATION.md).
Tools for alerts, incidents, services/traces and experiments are NOT registered because those backends do not exist yet:
an absent tool is honest, an empty success is not.

Per call, in order: verified bearer token (SDK middleware) -> scope check -> per-token rate limit -> workspace-scoped service
call in a worker thread -> audit event. The workspace comes from the token's server-side record, never from arguments.
"""
from __future__ import annotations

import threading
import time
import uuid
from collections import deque
from datetime import UTC, datetime
from typing import Annotated, Callable, Literal

from mcp.server.auth.middleware.auth_context import get_access_token
from mcp.server.auth.settings import AuthSettings
from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from pydantic import AnyHttpUrl, Field
from sqlalchemy.orm import Session, sessionmaker
from starlette.concurrency import run_in_threadpool

from app import errors
from app.mcp import service as S
from app.mcp.tokens import SdkTokenVerifier
from app.models import AuditEvent

RATE_LIMIT_CALLS = 60
RATE_LIMIT_WINDOW_S = 60.0


class RateLimiter:
    """Sliding window per token id. In-process only: with N API workers the effective limit is N x calls (documented)."""

    def __init__(self, calls: int = RATE_LIMIT_CALLS, window: float = RATE_LIMIT_WINDOW_S, monotonic: Callable[[], float] = time.monotonic):
        self.calls, self.window, self._now = calls, window, monotonic
        self._hits: dict[str, deque[float]] = {}
        self._lock = threading.Lock()

    def allow(self, key: str) -> bool:
        now = self._now()
        with self._lock:
            q = self._hits.setdefault(key, deque())
            while q and now - q[0] >= self.window:
                q.popleft()
            if len(q) >= self.calls:
                return False
            q.append(now)
            return True


def build_mcp_server(session_factory: sessionmaker[Session], clock: Callable[[], datetime] = lambda: datetime.now(UTC), *,
                     public_url: str, console_base_url: str | None = None, allowed_hosts: list[str] | None = None,
                     rate_limiter: RateLimiter | None = None) -> FastMCP:
    """`public_url` is this API's external base URL (used in the 401 WWW-Authenticate resource metadata pointer)."""
    limiter = rate_limiter or RateLimiter()
    hosts = allowed_hosts or []
    mcp = FastMCP(
        "UptimeTrace",
        instructions=("Read-only access to one UptimeTrace workspace: hosts, health and host metrics. Missing telemetry is reported "
                      "as unknown/null, never as zero. Use console_url fields to send the user to the UptimeTrace console."),
        token_verifier=SdkTokenVerifier(session_factory, clock),
        auth=AuthSettings(issuer_url=AnyHttpUrl(public_url), resource_server_url=AnyHttpUrl(public_url.rstrip("/") + "/mcp/"),
                          required_scopes=[]),
        streamable_http_path="/",       # mounted at /mcp by create_app
        stateless_http=True,            # no server-side session state: nothing to leak between callers, scales horizontally
        json_response=True,
        transport_security=TransportSecuritySettings(
            enable_dns_rebinding_protection=bool(hosts), allowed_hosts=hosts, allowed_origins=[]),
    )

    def run(tool: str, scope: str, args: dict, fn: Callable[[Session, uuid.UUID, datetime], dict]):
        """Authorisation + rate limit + audit wrapper. `fn` receives a session and the SERVER-resolved workspace id."""
        async def _go():
            token = get_access_token()
            if token is None or not token.claims:  # the SDK middleware already rejects this; defence in depth
                raise PermissionError("unauthenticated")
            if scope not in token.scopes:
                raise PermissionError(f"This token lacks the '{scope}' scope.")
            token_id = token.claims["token_id"]
            if not limiter.allow(token_id):
                raise RuntimeError(f"rate_limited: at most {limiter.calls} tool calls per {int(limiter.window)} s per token.")
            workspace = uuid.UUID(token.claims["workspace_id"])

            def work():
                actor = f"mcp:{token.claims['token_name']}"
                # Audited in its OWN transaction first, so a failing or rejected call still leaves its record.
                with session_factory() as audit, audit.begin():
                    audit.add(AuditEvent(workspace_id=workspace, actor=actor, action="mcp.tool_called", target_type="mcp_tool",
                                         target_id=tool, detail={"arguments": args, "token_id": token_id}))
                with session_factory() as session, session.begin():
                    try:
                        return fn(session, workspace, clock())
                    except errors.ApiError as exc:   # surface the platform's own error code and message to the agent
                        why = "; ".join(f"{d.path}: {d.message}" for d in (exc.details or []))
                        raise ValueError(f"{exc.code}: {exc.message}" + (f" ({why})" if why else "")) from None

            return await run_in_threadpool(work)
        return _go()

    @mcp.tool(description=("List hosts registered in the authenticated workspace with health, freshness and latest utilisation. "
                           "Hosts that have never reported have telemetry_state 'awaiting_first_telemetry' and health 'unknown'."))
    async def list_hosts(limit: Annotated[int, Field(ge=1, le=S.MAX_LIST)] = S.DEFAULT_LIST,
                         offset: Annotated[int, Field(ge=0)] = 0) -> dict:
        return await run("list_hosts", "hosts:read", {"limit": limit, "offset": offset},
                         lambda s, w, now: S.list_hosts(s, w, now, limit, offset, console_base_url))

    @mcp.tool(description=("Current health of one host: healthy, warning, critical, offline or unknown, with the evidence (metric, value, "
                           "threshold). 'offline' means no telemetry for 300 s; 'unknown' means no fresh cpu/memory/disk sample."))
    async def get_host_health(host_id: Annotated[str, Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")]) -> dict:
        return await run("get_host_health", "hosts:read", {"host_id": host_id},
                         lambda s, w, now: S.get_host_health(s, w, now, host_id, console_base_url))

    @mcp.tool(description=("Bounded historical series for one host metric (default last 1 h, at most 7 days and 500 points). Network "
                           "counters are returned as per-second rates. Null points are gaps (no data), never zero."))
    async def get_host_metrics(
        host_id: Annotated[str, Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")],
        metric: Literal["system.cpu.utilization", "system.memory.utilization", "system.disk.utilization",
                        "system.network.rx_bytes", "system.network.tx_bytes"],
        start: datetime | None = None, end: datetime | None = None,
        step_seconds: Annotated[int | None, Field(ge=10, le=86400)] = None,
        agg: Literal["avg", "min", "max", "last"] = "avg",
    ) -> dict:
        args = {"host_id": host_id, "metric": metric, "start": start.isoformat() if start else None,
                "end": end.isoformat() if end else None, "step_seconds": step_seconds, "agg": agg}
        return await run("get_host_metrics", "metrics:read", args,
                         lambda s, w, now: S.get_host_metrics(s, w, now, host_id, metric, start, end, step_seconds, agg, console_base_url))

    return mcp
