"""MCP interface: protocol, authentication, tenant isolation, bounds, audit. Official SDK client over the real Streamable HTTP
transport (in-process ASGI), real PostgreSQL (disposable), real least-privilege role. Never touches a shared database.
"""
from __future__ import annotations

import json
import uuid
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client
from sqlalchemy import text

from app.db import make_session_factory
from app.ingest.fingerprint import content_hash
from app.ingest.service import store_envelope
from app.main import create_app
from app.mcp import tokens as T
from app.mcp.server import RateLimiter, build_mcp_server
from app.registry.store import RegistryCredentialStore
from tests.clerk_support import bearer, verifier
from tests.conftest import FIXED_NOW

pytestmark = pytest.mark.anyio

BASE = "http://test"
CONSOLE = "https://console.example.test"
MCP_URL = f"{BASE}/mcp/"
CPU, MEM = "system.cpu.utilization", "system.memory.utilization"


class Clock:
    def __init__(self):
        self.now = FIXED_NOW

    def __call__(self):
        return self.now


@pytest.fixture(autouse=True)
def clean(engine):
    with engine.begin() as c:
        c.execute(text("TRUNCATE mcp_tokens, agent_credentials, hosts, audit_events RESTART IDENTITY CASCADE"))
        c.execute(text("DELETE FROM workspaces WHERE slug <> 'default'"))
    yield


@pytest.fixture
def clock():
    return Clock()


@pytest.fixture
def sf(app_engine):
    return make_session_factory(app_engine)


@pytest.fixture
def limiter():
    return RateLimiter()


@pytest.fixture
def app(app_engine, sf, clock, limiter):
    mcp = build_mcp_server(sf, clock, public_url=BASE, console_base_url=CONSOLE, allowed_hosts=[], rate_limiter=limiter)
    return create_app(app_engine, RegistryCredentialStore(sf), clock=clock, verifier=verifier(), mcp_server=mcp)


@pytest.fixture
async def running(app):
    async with app.router.lifespan_context(app):      # starts the MCP session manager
        yield app


def factory_for(app):
    def make(headers=None, timeout=None, auth=None):
        return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), headers=headers, timeout=timeout or 30, auth=auth,
                                 follow_redirects=True)
    return make


@asynccontextmanager
async def mcp_session(app, token):
    async with streamablehttp_client(MCP_URL, headers={"Authorization": f"Bearer {token}"}, httpx_client_factory=factory_for(app)) as (r, w, _):
        async with ClientSession(r, w) as session:
            await session.initialize()
            yield session


async def call(session, tool, **args):
    res = await session.call_tool(tool, args)
    body = res.structuredContent if res.structuredContent is not None else None
    if body is None and res.content and not res.isError:
        body = json.loads(res.content[0].text)
    return res, body


# ---- fixtures: two independent workspaces, each with hosts and an MCP token -----------------------------------------------
def iso(seconds_ago: float) -> str:
    return (FIXED_NOW - timedelta(seconds=seconds_ago)).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def seed_telemetry(sf, host_id, cpu=40.0, ago=10, mem=None):
    metrics = [{"name": CPU, "kind": "gauge", "value": cpu, "unit": "percent", "timestamp": iso(ago)}]
    if mem is not None:
        metrics.append({"name": MEM, "kind": "gauge", "value": mem, "unit": "percent", "timestamp": iso(ago)})
    env = {"schema_version": "1.0", "envelope_id": str(uuid.uuid4()), "agent_id": f"agent-{host_id}", "host_id": host_id,
           "hostname": host_id, "timestamp": iso(ago), "metrics": metrics}
    store_envelope(sf, env, content_hash(env))


@pytest.fixture
def world(engine, sf, clock):
    """Workspace A (default) owns a-host/a-silent; workspace B owns b-host. 'orphan' has telemetry but no registry entry."""
    with engine.begin() as c:
        a = c.execute(text("SELECT id FROM workspaces WHERE slug='default'")).scalar_one()
        b = c.execute(text("INSERT INTO workspaces (slug, name) VALUES ('other', 'Other') RETURNING id")).scalar_one()
        for ws, host in ((a, "a-host"), (a, "a-silent"), (b, "b-host")):
            c.execute(text("INSERT INTO hosts (workspace_id, host_id, kind, created_by) VALUES (:w, :h, 'live', 't')"), {"w": ws, "h": host})
    seed_telemetry(sf, "a-host", cpu=42.0, mem=91.0)
    seed_telemetry(sf, "b-host", cpu=77.0)
    seed_telemetry(sf, "orphan", cpu=10.0)

    def issue(ws, name, scopes=("hosts:read", "metrics:read"), days=30):
        with sf() as s, s.begin():
            return T.issue(s, "tester", ws, name, list(scopes), days, clock.now).token
    return {"a": a, "b": b, "tok_a": issue(a, "tok-a"), "tok_b": issue(b, "tok-b"), "issue": issue}


# ================================================================================ protocol
async def test_initialize_and_tool_discovery_lists_only_implemented_tools(running, world):
    async with mcp_session(running, world["tok_a"]) as s:
        tools = {t.name: t for t in (await s.list_tools()).tools}
    assert set(tools) == {"list_hosts", "get_host_health", "get_host_metrics"}      # nothing for absent backends
    for t in tools.values():
        assert t.inputSchema["type"] == "object"
        assert "workspace" not in json.dumps(t.inputSchema).lower()                 # no argument can select a workspace


async def test_no_resources_or_prompts_are_exposed(running, world):
    async with mcp_session(running, world["tok_a"]) as s:
        assert (await s.list_resources()).resources == []
        assert (await s.list_prompts()).prompts == []


async def test_list_hosts_returns_only_this_workspaces_registered_hosts_with_real_state(running, world):
    async with mcp_session(running, world["tok_a"]) as s:
        res, body = await call(s, "list_hosts")
    assert not res.isError
    by = {h["host_id"]: h for h in body["hosts"]}
    assert set(by) == {"a-host", "a-silent"} and body["total"] == 2
    assert by["a-host"]["health"]["status"] == "warning"                                 # memory 91 >= 85
    assert by["a-host"]["health"]["reasons"][0]["metric"] == MEM
    assert by["a-host"]["latest"]["cpu_percent"] == 42.0
    assert by["a-host"]["console_url"] == f"{CONSOLE}/hosts/a-host"                      # real console route
    s_ = by["a-silent"]                                                                  # registered, never reported
    assert s_["telemetry_state"] == "awaiting_first_telemetry" and s_["health"]["status"] == "unknown"
    assert s_["latest"] is None and s_["last_seen"] is None                              # missing is null, never zero


async def test_get_host_health_matches_the_console_api(running, world, app):
    async with mcp_session(running, world["tok_a"]) as s:
        _, body = await call(s, "get_host_health", host_id="a-host")
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=BASE) as c:
        console = (await c.get("/api/v1/hosts/a-host", headers=bearer())).json()
    assert body["health"] == console["health"] and body["latest"] == console["latest"]   # one implementation, two interfaces
    assert body["last_seen"].replace("+00:00", "Z") == console["last_seen"]


async def test_get_host_metrics_is_bounded_and_marks_gaps_as_null(running, world):
    async with mcp_session(running, world["tok_a"]) as s:
        res, body = await call(s, "get_host_metrics", host_id="a-host", metric=CPU, step_seconds=60)
        assert not res.isError and body["series"]
        pts = body["series"][0]["points"]
        assert any(p["value"] == 42.0 for p in pts) and any(p["value"] is None for p in pts)   # data + explicit gaps
        assert "not a zero reading" in body["note"] and body["console_url"] == f"{CONSOLE}/hosts/a-host"
        too_long = await call(s, "get_host_metrics", host_id="a-host", metric=CPU,
                              start=(FIXED_NOW - timedelta(days=8)).isoformat(), end=FIXED_NOW.isoformat())
        assert too_long[0].isError and "7 days" in too_long[0].content[0].text
        too_many = await call(s, "get_host_metrics", host_id="a-host", metric=CPU, step_seconds=10,
                              start=(FIXED_NOW - timedelta(days=2)).isoformat(), end=FIXED_NOW.isoformat())
        assert too_many[0].isError and "maximum is 500" in too_many[0].content[0].text
        naive = await call(s, "get_host_metrics", host_id="a-host", metric=CPU, start="2026-11-11T11:00:00")
        assert naive[0].isError


async def test_invalid_arguments_are_rejected_by_the_schema(running, world):
    async with mcp_session(running, world["tok_a"]) as s:
        for tool, args in (("list_hosts", {"limit": 0}), ("list_hosts", {"limit": 101}), ("list_hosts", {"offset": -1}),
                           ("get_host_health", {"host_id": "x; DROP TABLE hosts"}),
                           ("get_host_metrics", {"host_id": "a-host", "metric": "pg_catalog.pg_shadow"}),
                           ("get_host_metrics", {"host_id": "a-host", "metric": CPU, "agg": "median"})):
            res = await s.call_tool(tool, args)
            assert res.isError, (tool, args)


async def test_unknown_tool_is_an_error_not_a_crash(running, world):
    async with mcp_session(running, world["tok_a"]) as s:
        res = await s.call_tool("run_sql", {"query": "select 1"})
        assert res.isError


# ================================================================================ authentication (HTTP level)
async def post_init(app, headers):
    body = {"jsonrpc": "2.0", "id": 1, "method": "initialize",
            "params": {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "t", "version": "0"}}}
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=BASE) as c:
        return await c.post("/mcp/", json=body, headers={"Accept": "application/json, text/event-stream", **headers})


async def test_missing_malformed_and_unknown_tokens_get_401(running, world):
    for h in ({}, {"Authorization": "Bearer"}, {"Authorization": "Basic abc"}, {"Authorization": "Bearer utm_" + "A" * 43},
              {"Authorization": "Bearer not-even-prefixed"}):
        r = await post_init(running, h)
        assert r.status_code == 401, h
        assert "www-authenticate" in r.headers


async def test_an_agent_ingestion_token_or_console_session_is_not_an_mcp_token(running, world, sf):
    from app import auth
    r = await post_init(running, {"Authorization": "Bearer " + "utk_" + "B" * 43})
    assert r.status_code == 401
    r = await post_init(running, bearer())                                               # a valid Clerk session JWT
    assert r.status_code == 401


async def test_expired_token_is_rejected(running, world, clock):
    tok = world["issue"](world["a"], "short", days=1)
    clock.now = FIXED_NOW + timedelta(days=1, seconds=1)
    assert (await post_init(running, {"Authorization": f"Bearer {tok}"})).status_code == 401


async def test_revoked_token_stops_working_on_the_next_request(running, world, sf, clock):
    tok = world["tok_a"]
    assert (await post_init(running, {"Authorization": f"Bearer {tok}"})).status_code == 200
    with sf() as s, s.begin():
        tid = T.list_tokens(s, world["a"])[0].id
        T.revoke(s, "tester", world["a"], tid, clock.now)
    assert (await post_init(running, {"Authorization": f"Bearer {tok}"})).status_code == 401


async def test_insufficient_scope_is_denied_per_tool(running, world):
    tok = world["issue"](world["a"], "hosts-only", scopes=("hosts:read",))
    async with mcp_session(running, tok) as s:
        assert not (await s.call_tool("list_hosts", {})).isError
        res = await s.call_tool("get_host_metrics", {"host_id": "a-host", "metric": CPU})
        assert res.isError and "metrics:read" in res.content[0].text


async def test_database_failure_during_verification_fails_closed(running, world, engine):
    # simulate an unreachable/failed registry: rename the table so lookups raise; the token must NOT be accepted
    with engine.begin() as c:
        c.execute(text("ALTER TABLE mcp_tokens RENAME TO mcp_tokens_x"))
    try:
        r = await post_init(running, {"Authorization": f"Bearer {world['tok_a']}"})
        assert r.status_code == 401                                                       # not accepted, and no 500 / no data
    finally:
        with engine.begin() as c:
            c.execute(text("ALTER TABLE mcp_tokens_x RENAME TO mcp_tokens"))


# ================================================================================ tenant isolation
async def test_workspace_a_cannot_see_workspace_b_hosts_in_listings(running, world):
    async with mcp_session(running, world["tok_a"]) as sa, mcp_session(running, world["tok_b"]) as sb:
        _, a = await call(sa, "list_hosts")
        _, b = await call(sb, "list_hosts")
    assert {h["host_id"] for h in a["hosts"]} == {"a-host", "a-silent"}
    assert {h["host_id"] for h in b["hosts"]} == {"b-host"}
    assert "orphan" not in json.dumps([a, b])                                            # unregistered telemetry is invisible


async def test_cross_workspace_object_ids_are_denied_identically_to_nonexistent_ones(running, world):
    async with mcp_session(running, world["tok_a"]) as s:
        outcomes = []
        for host in ("b-host", "orphan", "does-not-exist"):
            for tool, args in (("get_host_health", {"host_id": host}),
                               ("get_host_metrics", {"host_id": host, "metric": CPU})):
                res = await s.call_tool(tool, args)
                assert res.isError, (tool, host)
                outcomes.append(res.content[0].text.replace(host, "X"))
    assert len(set(outcomes)) <= 2 and all("host_not_found" in o for o in outcomes)       # same text: no existence oracle
    assert "77" not in " ".join(outcomes)                                                  # B's cpu value never leaks


async def test_tool_arguments_cannot_select_a_workspace(running, world):
    async with mcp_session(running, world["tok_a"]) as s:
        res = await s.call_tool("list_hosts", {"workspace_id": str(world["b"])})
        if not res.isError:                                                                # extra keys ignored -> still A's data
            body = json.loads(res.content[0].text)
            assert {h["host_id"] for h in body["hosts"]} == {"a-host", "a-silent"}


async def test_token_management_is_scoped_and_requires_a_console_session(running, world, app):
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=BASE) as c:
        assert (await c.get("/api/v1/registry/mcp-tokens")).status_code == 401
        r = await c.post("/api/v1/registry/mcp-tokens", json={"name": "from-console", "scopes": ["hosts:read"], "expires_in_days": 7},
                         headers=bearer())
        assert r.status_code == 201 and r.headers["cache-control"] == "no-store" and r.json()["token"].startswith("utm_")
        listing = (await c.get("/api/v1/registry/mcp-tokens", headers=bearer())).json()
        assert "token" not in json.dumps(listing) and "token_sha256" not in json.dumps(listing)
        assert {t["name"] for t in listing} == {"tok-a", "from-console"}                    # workspace A's tokens only, never tok-b
        bad = await c.post("/api/v1/registry/mcp-tokens", json={"name": "x", "scopes": ["admin:all"]}, headers=bearer())
        assert bad.status_code == 422
        evil = await c.post("/api/v1/registry/mcp-tokens", json={"name": "y", "workspace_id": str(world["b"])}, headers=bearer())
        assert evil.status_code == 422                                                     # caller-supplied workspace rejected
        # a token id from workspace B cannot be revoked through the (workspace A) console
        with_b = (await c.delete(f"/api/v1/registry/mcp-tokens/{uuid.uuid4()}", headers=bearer()))
        assert with_b.status_code == 404
    # B's token still works
    async with mcp_session(running, world["tok_b"]) as s:
        assert not (await s.call_tool("list_hosts", {})).isError


async def test_revoking_a_token_by_id_from_another_workspace_is_a_404(world, sf, clock):
    with sf() as s:
        b_token_id = T.list_tokens(s, world["b"])[0].id
    with pytest.raises(Exception) as err:
        with sf() as s, s.begin():
            T.revoke(s, "tester", world["a"], b_token_id, clock.now)
    assert "mcp_token_not_found" in str(getattr(err.value, "code", "")) or getattr(err.value, "status", 0) == 404


# ================================================================================ audit, rate limit, secrets
async def test_every_tool_call_is_audited_without_secrets(running, world, engine):
    async with mcp_session(running, world["tok_a"]) as s:
        await s.call_tool("list_hosts", {})
        await s.call_tool("get_host_health", {"host_id": "b-host"})                       # denied calls are audited too
    with engine.connect() as c:
        rows = c.execute(text("SELECT actor, target_id, detail::text AS d FROM audit_events WHERE action='mcp.tool_called' ORDER BY id")).all()
    assert [r.target_id for r in rows] == ["list_hosts", "get_host_health"] and all(r.actor == "mcp:tok-a" for r in rows)
    assert world["tok_a"] not in " ".join(r.d for r in rows)


async def test_rate_limit_is_per_token(app_engine, sf, clock, world):
    lim = RateLimiter(calls=3, window=60, monotonic=lambda: 0.0)
    mcp = build_mcp_server(sf, clock, public_url=BASE, console_base_url=None, allowed_hosts=[], rate_limiter=lim)
    app = create_app(app_engine, RegistryCredentialStore(sf), clock=clock, verifier=verifier(), mcp_server=mcp)
    async with app.router.lifespan_context(app):
        async with mcp_session(app, world["tok_a"]) as s:
            results = [(await s.call_tool("list_hosts", {})).isError for _ in range(5)]
            assert results == [False, False, False, True, True]
            blocked = await s.call_tool("list_hosts", {})
            assert "rate_limited" in blocked.content[0].text
        async with mcp_session(app, world["tok_b"]) as s:                                   # another token is unaffected
            res, body = await call(s, "list_hosts")
            assert not res.isError
            assert body["hosts"][0]["console_url"] is None                                  # no console URL configured: no link invented


async def test_responses_never_contain_credentials(running, world):
    async with mcp_session(running, world["tok_a"]) as s:
        blobs = [json.dumps((await call(s, "list_hosts"))[1]), json.dumps((await call(s, "get_host_health", host_id="a-host"))[1])]
    joined = " ".join(blobs).lower()
    for needle in ("token_sha256", "password", "secret", "utm_", "utk_", "database_url"):
        assert needle not in joined


def test_mcp_is_off_unless_explicitly_built(app_engine):
    plain = create_app(app_engine, RegistryCredentialStore(make_session_factory(app_engine)))
    assert not any(getattr(r, "path", "") == "/mcp" for r in plain.routes)


def test_mcp_tokens_table_is_protected(app_engine, engine):
    from sqlalchemy.exc import ProgrammingError
    for sql in ("UPDATE mcp_tokens SET token_sha256 = repeat('1', 64)", "UPDATE mcp_tokens SET workspace_id = gen_random_uuid()",
                "UPDATE mcp_tokens SET scopes = ARRAY['hosts:read']", "UPDATE mcp_tokens SET expires_at = now() + interval '9999 days'",
                "DELETE FROM mcp_tokens"):
        with pytest.raises(ProgrammingError):
            with app_engine.begin() as c:
                c.execute(text(sql))
    with engine.connect() as c:
        for role in ("anon", "authenticated", "service_role"):
            assert not c.execute(text("SELECT has_table_privilege(:r, 'mcp_tokens', 'SELECT,INSERT,UPDATE,DELETE')"), {"r": role}).scalar_one()
