"""M1: host registry, registry-issued agent credentials, immediate revocation, audit trail, least-privilege grants.

Real PostgreSQL (the disposable test database), the real least-privilege `uptimetrace_app` role, the real ingestion pipeline.
"""
from __future__ import annotations

import uuid

import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.exc import ProgrammingError

from app.db import make_session_factory
from app.main import create_app
from app.registry.store import RegistryCredentialStore
from tests.clerk_support import OTHER_USER, bearer, verifier
from tests.conftest import FIXED_NOW
from tests.factories import make_envelope, raw

pytestmark = pytest.mark.anyio

JSON = {"content-type": "application/json"}


@pytest.fixture(autouse=True)
def clean_registry(engine):
    with engine.begin() as c:
        c.execute(text("TRUNCATE agent_credentials, hosts, audit_events RESTART IDENTITY CASCADE"))
    yield


@pytest.fixture
def api(app_engine):
    store = RegistryCredentialStore(make_session_factory(app_engine))
    app = create_app(app_engine, store, clock=lambda: FIXED_NOW, verifier=verifier())
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app, raise_app_exceptions=False), base_url="http://test")


async def register(api, host_id="host-test", **body):
    return await api.post("/api/v1/registry/hosts", json={"host_id": host_id, **body}, headers=bearer())


async def issue(api, host_id="host-test", **body):
    return await api.post(f"/api/v1/registry/hosts/{host_id}/credentials", json=body, headers=bearer())


async def send(api, token, **env):
    env = {"host_id": "host-test", "agent_id": "agent-host-test", **env}   # the registry's default agent id is "agent-<host_id>"
    return await api.post("/api/v1/telemetry", content=raw(make_envelope(**env)), headers={**JSON, "authorization": f"Bearer {token}"})


# ------------------------------------------------------------------------------------------------------ the M1 journey
async def test_register_issue_ingest_see_host_then_revoke_takes_effect_immediately(api):
    r = await register(api, display_name="Test box")
    assert r.status_code == 201
    assert r.json()["telemetry_state"] == "awaiting_first_telemetry" and r.json()["active_credentials"] == 0
    assert r.json()["last_envelope_at"] is None

    r = await issue(api)
    assert r.status_code == 201 and r.headers["cache-control"] == "no-store"
    cred = r.json()
    token = cred["token"]
    assert token.startswith("utk_") and "token_sha256" not in cred and cred["agent_id"] == "agent-host-test"

    assert (await send(api, token)).status_code == 202            # accepted for exactly the registered host + agent id
    host = (await api.get("/api/v1/registry/hosts", headers=bearer())).json()[0]
    assert host["telemetry_state"] == "reporting" and host["active_credentials"] == 1

    # the host now appears in the read API with its telemetry
    read = await api.get("/api/v1/hosts/host-test", headers=bearer())
    assert read.status_code == 200 and read.json()["reporting_status"] == "reporting"

    r = await api.delete(f"/api/v1/registry/hosts/host-test/credentials/{cred['id']}", headers=bearer())
    assert r.status_code == 200 and r.json()["revoked_at"] is not None
    resp = await send(api, token)                                  # same token, next request, no restart
    assert resp.status_code == 401 and token not in resp.text


async def test_credential_is_bound_to_its_agent_and_host(api):
    await register(api, "host-a")
    await register(api, "host-b")
    token = (await issue(api, "host-a")).json()["token"]
    assert (await send(api, token, host_id="host-b", agent_id="agent-host-a")).status_code == 403
    assert (await send(api, token, host_id="host-a", agent_id="someone-else")).status_code == 403
    assert (await send(api, token, host_id="host-a", agent_id="agent-host-a")).status_code == 202


async def test_unregistered_or_unknown_token_is_rejected_and_listing_never_exposes_secrets(api):
    await register(api)
    cred = (await issue(api)).json()
    assert (await send(api, "utk_" + "A" * 43)).status_code == 401
    listing = (await api.get("/api/v1/registry/hosts/host-test/credentials", headers=bearer())).json()
    assert len(listing) == 1 and set(listing[0]) == {"id", "name", "agent_id", "created_at", "created_by", "revoked_at", "revoked_by"}
    assert cred["token"] not in str(listing)


async def test_revocation_is_idempotent_and_keeps_the_first_revocation(api):
    await register(api)
    cid = (await issue(api)).json()["id"]
    first = (await api.delete(f"/api/v1/registry/hosts/host-test/credentials/{cid}", headers=bearer())).json()
    second = (await api.delete(f"/api/v1/registry/hosts/host-test/credentials/{cid}", headers=bearer())).json()
    assert first["revoked_at"] == second["revoked_at"] and first["revoked_by"] == second["revoked_by"]
    missing = await api.delete(f"/api/v1/registry/hosts/host-test/credentials/{uuid.uuid4()}", headers=bearer())
    assert missing.status_code == 404 and missing.json()["error"]["code"] == "credential_not_found"


async def test_one_revocation_does_not_affect_another_credential_of_the_same_host(api):
    await register(api)
    old, new = (await issue(api)).json(), (await issue(api)).json()      # rotation overlap
    await api.delete(f"/api/v1/registry/hosts/host-test/credentials/{old['id']}", headers=bearer())
    assert (await send(api, old["token"])).status_code == 401
    assert (await send(api, new["token"])).status_code == 202


# ------------------------------------------------------------------------------------------------------ validation
async def test_host_registration_rules(api):
    assert (await register(api, "bad host!")).status_code == 422
    assert (await register(api, "sim-1", kind="live")).status_code == 422          # reserved prefix for a live host
    assert (await register(api, "web-1", kind="synthetic")).status_code == 422     # synthetic hosts must start with sim-
    assert (await register(api, "sim-1", kind="synthetic")).status_code == 201
    assert (await register(api, "sim-1", kind="synthetic")).json()["error"]["code"] == "host_exists"
    r = await api.post("/api/v1/registry/hosts", json={"host_id": "x", "workspace_id": "evil"}, headers=bearer())
    assert r.status_code == 422                                                    # a caller-supplied workspace id is never accepted


async def test_credential_limits_and_unknown_host(api):
    assert (await issue(api, "nope")).json()["error"]["code"] == "host_not_registered"
    await register(api)
    for _ in range(5):
        assert (await issue(api)).status_code == 201
    assert (await issue(api)).json()["error"]["code"] == "credential_limit"
    assert (await issue(api, name="bad name!")).status_code == 422


async def test_duplicate_credential_name_is_a_conflict(api):
    await register(api)
    assert (await issue(api, name="agent-one")).status_code == 201
    assert (await issue(api, name="agent-one")).json()["error"]["code"] == "credential_name_taken"


# ------------------------------------------------------------------------------------------------------ authorization
async def test_registry_requires_an_authorised_session(api):
    assert (await api.get("/api/v1/registry/hosts")).status_code == 401
    assert (await api.post("/api/v1/registry/hosts", json={"host_id": "x"})).status_code == 401
    stranger = bearer(sub=OTHER_USER)
    assert (await api.get("/api/v1/registry/hosts", headers=stranger)).status_code == 403
    assert (await api.post("/api/v1/registry/hosts", json={"host_id": "x"}, headers=stranger)).status_code == 403
    assert (await api.delete(f"/api/v1/registry/hosts/x/credentials/{uuid.uuid4()}", headers=stranger)).status_code == 403


async def test_an_agent_token_is_not_a_browser_session(api):
    await register(api)
    token = (await issue(api)).json()["token"]
    r = await api.get("/api/v1/registry/hosts", headers={"authorization": f"Bearer {token}"})
    assert r.status_code == 401


# ------------------------------------------------------------------------------------------------------ audit + database privileges
async def test_every_mutation_is_audited_without_secrets(api, engine):
    await register(api)
    cred = (await issue(api)).json()
    await api.delete(f"/api/v1/registry/hosts/host-test/credentials/{cred['id']}", headers=bearer())
    with engine.connect() as c:
        rows = c.execute(text("SELECT actor, action, target_type, detail::text AS d FROM audit_events ORDER BY id")).all()
        sha = c.execute(text("SELECT token_sha256 FROM agent_credentials")).scalar_one()
    assert [r.action for r in rows] == ["host.registered", "credential.issued", "credential.revoked"]
    assert all(r.actor == "user_allowed_1" for r in rows)
    blob = " ".join(r.d for r in rows)
    assert cred["token"] not in blob and sha not in blob


def test_app_role_cannot_tamper_with_credentials_or_the_audit_log(app_engine, engine):
    with engine.begin() as c:
        ws = c.execute(text("SELECT id FROM workspaces WHERE slug='default'")).scalar_one()
        hid = c.execute(text("INSERT INTO hosts (workspace_id, host_id, kind, created_by) VALUES (:w,'h1','live','t') RETURNING id"), {"w": ws}).scalar_one()
        c.execute(text("INSERT INTO agent_credentials (workspace_id, host_pk, name, agent_id, token_sha256, created_by) "
                       "VALUES (:w,:h,'c1','a1',:d,'t')"), {"w": ws, "h": hid, "d": "0" * 64})
        c.execute(text("INSERT INTO audit_events (workspace_id, actor, action, target_type, target_id) VALUES (:w,'t','x','host','h1')"), {"w": ws})
    forbidden = [
        "UPDATE agent_credentials SET token_sha256 = repeat('1', 64)",     # hashes are immutable
        "UPDATE agent_credentials SET agent_id = 'evil'",
        "DELETE FROM agent_credentials",
        "UPDATE audit_events SET actor = 'forged'",                        # append-only
        "DELETE FROM audit_events",
        "DELETE FROM hosts",
        "UPDATE hosts SET host_id = 'renamed'",                            # identity is immutable
        "UPDATE workspaces SET slug = 'x'",
        "TRUNCATE audit_events",
    ]
    for sql in forbidden:
        with pytest.raises(ProgrammingError):
            with app_engine.begin() as c:
                c.execute(text(sql))
    with app_engine.begin() as c:                                           # what the application legitimately does
        c.execute(text("UPDATE agent_credentials SET revoked_at = now(), revoked_by = 'u'"))


def test_data_api_roles_have_no_access_to_the_new_tables(engine):
    with engine.connect() as c:
        for role in ("anon", "authenticated", "service_role"):
            for table in ("workspaces", "hosts", "agent_credentials", "audit_events"):
                assert not c.execute(text("SELECT has_table_privilege(:r, :t, 'SELECT,INSERT,UPDATE,DELETE')"), {"r": role, "t": table}).scalar_one(), (role, table)
        assert c.execute(text("SELECT relrowsecurity FROM pg_class WHERE relname = 'agent_credentials'")).scalar_one()
