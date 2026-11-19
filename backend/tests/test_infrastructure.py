"""Infrastructure inventory: environments, regions, clusters, services and their relationships.

Real PostgreSQL, the real least-privilege application role, the real registry and ingestion paths. Cross-workspace checks run at two
layers: the API must refuse, and the database's composite foreign keys must reject the same reference written directly.
"""
from __future__ import annotations

import uuid
from datetime import timedelta

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from tests.clerk_support import OTHER_USER, bearer
from tests.test_alerts import CPU, HOST, T0, alerts, metric, provision, send, ts, world  # noqa: F401 (fixture)

pytestmark = pytest.mark.anyio


@pytest.fixture(autouse=True)
def clean_inventory(engine):
    with engine.begin() as c:
        c.execute(text("TRUNCATE service_hosts, services, clusters, regions, environments, investigation_reports, alert_events, alerts, "
                       "monitoring_settings, telemetry_envelopes, agent_credentials, hosts, audit_events RESTART IDENTITY CASCADE"))
        c.execute(text("DELETE FROM workspaces WHERE slug <> 'default'"))
    yield


async def api(w, method, url, body=None):
    r = await w["client"].request(method, url, json=body, headers=bearer())
    return r


async def inventory(w):
    r = await api(w, "GET", "/api/v1/infrastructure")
    assert r.status_code == 200, r.text
    return r.json()


async def make(w, url, body):
    r = await api(w, "POST", url, body)
    assert r.status_code == 201, r.text
    return r.json()


def find_env(inv, name):
    return next(e for e in inv["environments"] if e["name"] == name)


# ----------------------------------------------------------------------------------------- hierarchy and optional grouping
async def test_multiple_environments_regions_and_clusters_nest_correctly(world):
    prod = await make(world, "/api/v1/infrastructure/environments", {"name": "production"})
    stg = await make(world, "/api/v1/infrastructure/environments", {"name": "staging"})
    eu = await make(world, "/api/v1/infrastructure/regions", {"name": "eu-west-1", "environment_id": prod["id"]})
    us = await make(world, "/api/v1/infrastructure/regions", {"name": "us-east-1", "environment_id": prod["id"]})
    await make(world, "/api/v1/infrastructure/regions", {"name": "stg-1", "environment_id": stg["id"]})
    await make(world, "/api/v1/infrastructure/clusters", {"name": "api-eu", "region_id": eu["id"]})
    await make(world, "/api/v1/infrastructure/clusters", {"name": "api-us", "region_id": us["id"]})
    inv = await inventory(world)
    p = find_env(inv, "production")
    assert [r["name"] for r in p["regions"]] == ["eu-west-1", "us-east-1"]
    assert [c["name"] for c in p["regions"][0]["clusters"]] == ["api-eu"]
    assert [r["name"] for r in find_env(inv, "staging")["regions"]] == ["stg-1"]


async def test_grouping_is_optional_and_unassigned_items_are_visible(world):
    await provision(world, host_id="loose-01")
    region = await make(world, "/api/v1/infrastructure/regions", {"name": "no-env-region"})
    cluster = await make(world, "/api/v1/infrastructure/clusters", {"name": "no-region-cluster"})
    await make(world, "/api/v1/infrastructure/services", {"name": "frontend"})
    inv = await inventory(world)
    assert [h["host_id"] for h in inv["ungrouped_hosts"]] == ["loose-01"]
    assert [r["name"] for r in inv["unassigned_regions"]] == ["no-env-region"]
    assert [c["name"] for c in inv["unassigned_clusters"]] == ["no-region-cluster"]
    assert [s["name"] for s in inv["unassigned_services"]] == ["frontend"]
    assert region["id"] and cluster["id"]


async def test_existing_host_without_metadata_still_appears_and_is_not_fabricated(world):
    await provision(world, host_id="legacy-01")
    inv = await inventory(world)
    host = inv["ungrouped_hosts"][0]
    assert host == {"host_id": "legacy-01", "display_name": None, "kind": "live", "deactivated": False}
    assert inv["environments"] == [] and inv["services"] == []


async def test_synthetic_hosts_keep_their_kind_and_are_not_presented_as_live(world):
    await provision(world, host_id="sim01-web", kind="synthetic")
    inv = await inventory(world)
    assert inv["ungrouped_hosts"][0]["kind"] == "synthetic"


# ------------------------------------------------------------------------------------------------ host membership
async def test_many_hosts_per_cluster_and_one_cluster_per_host(world):
    await provision(world, host_id="api-1")
    await provision(world, host_id="api-2")
    c1 = await make(world, "/api/v1/infrastructure/clusters", {"name": "api"})
    c2 = await make(world, "/api/v1/infrastructure/clusters", {"name": "worker"})
    assert (await api(world, "POST", f"/api/v1/infrastructure/clusters/{c1['id']}/hosts", {"host_id": "api-1"})).status_code == 201
    assert (await api(world, "POST", f"/api/v1/infrastructure/clusters/{c1['id']}/hosts", {"host_id": "api-2"})).status_code == 201
    inv = await inventory(world)
    api_cluster = next(c for c in inv["unassigned_clusters"] if c["name"] == "api")
    assert sorted(h["host_id"] for h in api_cluster["hosts"]) == ["api-1", "api-2"]
    assert inv["ungrouped_hosts"] == []
    r = await api(world, "POST", f"/api/v1/infrastructure/clusters/{c2['id']}/hosts", {"host_id": "api-1"})
    assert r.status_code == 409 and r.json()["error"]["code"] == "host_in_other_cluster"


async def test_duplicate_membership_is_refused(world):
    await provision(world, host_id="api-1")
    c1 = await make(world, "/api/v1/infrastructure/clusters", {"name": "api"})
    await api(world, "POST", f"/api/v1/infrastructure/clusters/{c1['id']}/hosts", {"host_id": "api-1"})
    r = await api(world, "POST", f"/api/v1/infrastructure/clusters/{c1['id']}/hosts", {"host_id": "api-1"})
    assert r.status_code == 409 and r.json()["error"]["code"] == "already_in_cluster"


async def test_removing_a_host_from_its_cluster_returns_it_to_ungrouped(world):
    await provision(world, host_id="api-1")
    c1 = await make(world, "/api/v1/infrastructure/clusters", {"name": "api"})
    await api(world, "POST", f"/api/v1/infrastructure/clusters/{c1['id']}/hosts", {"host_id": "api-1"})
    r = await api(world, "DELETE", f"/api/v1/infrastructure/clusters/{c1['id']}/hosts/api-1")
    assert r.status_code == 200
    assert [h["host_id"] for h in (await inventory(world))["ungrouped_hosts"]] == ["api-1"]
    r2 = await api(world, "DELETE", f"/api/v1/infrastructure/clusters/{c1['id']}/hosts/api-1")
    assert r2.status_code == 404 and r2.json()["error"]["code"] == "not_in_cluster"


# -------------------------------------------------------------------------------------------- services and host links
async def test_a_service_spans_hosts_and_a_host_runs_several_services(world):
    await provision(world, host_id="h1")
    await provision(world, host_id="h2")
    checkout = await make(world, "/api/v1/infrastructure/services", {"name": "checkout"})
    worker = await make(world, "/api/v1/infrastructure/services", {"name": "worker"})
    await api(world, "POST", f"/api/v1/infrastructure/services/{checkout['id']}/hosts", {"host_id": "h1"})
    await api(world, "POST", f"/api/v1/infrastructure/services/{checkout['id']}/hosts", {"host_id": "h2"})
    await api(world, "POST", f"/api/v1/infrastructure/services/{worker['id']}/hosts", {"host_id": "h1"})
    inv = await inventory(world)
    by_name = {s["name"]: s for s in inv["services"]}
    assert sorted(h["host_id"] for h in by_name["checkout"]["hosts"]) == ["h1", "h2"]
    assert sorted(s["name"] for s in inv["services"] if any(h["host_id"] == "h1" for h in s["hosts"])) == ["checkout", "worker"]
    assert inv["unassigned_services"] == []


async def test_a_host_can_belong_to_a_service_without_a_cluster(world):
    await provision(world, host_id="h1")
    svc = await make(world, "/api/v1/infrastructure/services", {"name": "redis"})
    await api(world, "POST", f"/api/v1/infrastructure/services/{svc['id']}/hosts", {"host_id": "h1"})
    inv = await inventory(world)
    assert [h["host_id"] for h in inv["ungrouped_hosts"]] == ["h1"]
    assert inv["services"][0]["hosts"][0]["host_id"] == "h1"


async def test_duplicate_service_link_and_unknown_host_are_refused(world):
    await provision(world, host_id="h1")
    svc = await make(world, "/api/v1/infrastructure/services", {"name": "redis"})
    await api(world, "POST", f"/api/v1/infrastructure/services/{svc['id']}/hosts", {"host_id": "h1"})
    dup = await api(world, "POST", f"/api/v1/infrastructure/services/{svc['id']}/hosts", {"host_id": "h1"})
    assert dup.status_code == 409 and dup.json()["error"]["code"] == "already_linked"
    missing = await api(world, "POST", f"/api/v1/infrastructure/services/{svc['id']}/hosts", {"host_id": "nope-9"})
    assert missing.status_code == 404 and missing.json()["error"]["code"] == "host_not_found"


async def test_unlinking_a_service_from_a_host_leaves_both_intact(world):
    await provision(world, host_id="h1")
    svc = await make(world, "/api/v1/infrastructure/services", {"name": "redis"})
    await api(world, "POST", f"/api/v1/infrastructure/services/{svc['id']}/hosts", {"host_id": "h1"})
    r = await api(world, "DELETE", f"/api/v1/infrastructure/services/{svc['id']}/hosts/h1")
    assert r.status_code == 200
    inv = await inventory(world)
    assert inv["services"][0]["hosts"] == []
    assert [s["name"] for s in inv["unassigned_services"]] == ["redis"]
    assert [h["host_id"] for h in inv["ungrouped_hosts"]] == ["h1"]


# ----------------------------------------------------------------------------------------------- validation and names
async def test_duplicate_names_within_the_same_parent_are_refused(world):
    await make(world, "/api/v1/infrastructure/environments", {"name": "production"})
    r = await api(world, "POST", "/api/v1/infrastructure/environments", {"name": "production"})
    assert r.status_code == 409 and r.json()["error"]["code"] == "name_taken"


async def test_unassigned_regions_with_the_same_name_are_refused_too(world):
    await make(world, "/api/v1/infrastructure/regions", {"name": "eu-west-1"})
    r = await api(world, "POST", "/api/v1/infrastructure/regions", {"name": "eu-west-1"})
    assert r.status_code == 409 and r.json()["error"]["code"] == "name_taken"


async def test_invalid_names_and_fields_are_rejected_before_any_write(world):
    assert (await api(world, "POST", "/api/v1/infrastructure/environments", {"name": "   "})).status_code == 422
    assert (await api(world, "POST", "/api/v1/infrastructure/environments", {"name": "x" * 65})).status_code == 422
    assert (await api(world, "POST", "/api/v1/infrastructure/services", {"name": "ok", "owner": "me"})).status_code == 422
    assert (await inventory(world))["environments"] == []


async def test_a_reference_to_a_missing_parent_is_a_clear_not_found(world):
    r = await api(world, "POST", "/api/v1/infrastructure/regions", {"name": "r", "environment_id": str(uuid.uuid4())})
    assert r.status_code == 404 and r.json()["error"]["code"] == "environment_not_found"


async def test_explicit_null_detaches_a_region_and_omitted_fields_are_unchanged(world):
    env = await make(world, "/api/v1/infrastructure/environments", {"name": "production"})
    region = await make(world, "/api/v1/infrastructure/regions", {"name": "eu-west-1", "environment_id": env["id"]})
    r = await api(world, "PATCH", f"/api/v1/infrastructure/regions/{region['id']}", {"environment_id": None})
    assert r.status_code == 200
    inv = await inventory(world)
    assert find_env(inv, "production")["regions"] == []
    assert [r["name"] for r in inv["unassigned_regions"]] == ["eu-west-1"]
    r2 = await api(world, "PATCH", f"/api/v1/infrastructure/regions/{region['id']}", {"name": "eu-west-2"})
    assert r2.status_code == 200
    assert (await inventory(world))["unassigned_regions"][0]["name"] == "eu-west-2"


async def test_renaming_into_a_duplicate_name_is_refused(world):
    await make(world, "/api/v1/infrastructure/services", {"name": "api"})
    s2 = await make(world, "/api/v1/infrastructure/services", {"name": "worker"})
    r = await api(world, "PATCH", f"/api/v1/infrastructure/services/{s2['id']}", {"name": "api"})
    assert r.status_code == 409 and r.json()["error"]["code"] == "name_taken"


# ------------------------------------------------------------------------------- deletion keeps telemetry, alerts, hosts
async def test_deleting_a_cluster_detaches_hosts_and_keeps_telemetry_and_alerts(world, engine):
    tok = await provision(world, host_id="api-1")
    await send(world, tok, "api-1", [metric(CPU, 22.0, T0 - timedelta(minutes=1))], T0 - timedelta(minutes=1))
    await send(world, tok, "api-1", [metric(CPU, 97.0, T0)], T0)
    assert (await alerts(world, "all"))
    c1 = await make(world, "/api/v1/infrastructure/clusters", {"name": "api"})
    await api(world, "POST", f"/api/v1/infrastructure/clusters/{c1['id']}/hosts", {"host_id": "api-1"})
    with engine.connect() as c:
        before = (c.execute(text("SELECT count(*) FROM telemetry_envelopes")).scalar_one(),
                  c.execute(text("SELECT count(*) FROM alerts")).scalar_one())
    r = await api(world, "DELETE", f"/api/v1/infrastructure/clusters/{c1['id']}")
    assert r.status_code == 200
    with engine.connect() as c:
        after = (c.execute(text("SELECT count(*) FROM telemetry_envelopes")).scalar_one(),
                 c.execute(text("SELECT count(*) FROM alerts")).scalar_one())
        cluster_id = c.execute(text("SELECT cluster_id FROM hosts WHERE host_id = 'api-1'")).scalar_one()
    assert after == before and before[0] > 0 and before[1] > 0
    assert cluster_id is None
    assert [h["host_id"] for h in (await inventory(world))["ungrouped_hosts"]] == ["api-1"]


async def test_deleting_an_environment_detaches_regions_and_deleting_a_service_removes_only_its_links(world):
    await provision(world, host_id="h1")
    env = await make(world, "/api/v1/infrastructure/environments", {"name": "production"})
    await make(world, "/api/v1/infrastructure/regions", {"name": "eu-west-1", "environment_id": env["id"]})
    svc = await make(world, "/api/v1/infrastructure/services", {"name": "redis"})
    await api(world, "POST", f"/api/v1/infrastructure/services/{svc['id']}/hosts", {"host_id": "h1"})
    assert (await api(world, "DELETE", f"/api/v1/infrastructure/environments/{env['id']}")).status_code == 200
    assert (await api(world, "DELETE", f"/api/v1/infrastructure/services/{svc['id']}")).status_code == 200
    inv = await inventory(world)
    assert [r["name"] for r in inv["unassigned_regions"]] == ["eu-west-1"]
    assert inv["services"] == []
    assert [h["host_id"] for h in inv["ungrouped_hosts"]] == ["h1"]


# ------------------------------------------------------------------------------------------------ workspace boundaries
def _foreign_workspace(engine) -> dict:
    """A second workspace with its own host and cluster, written with the owner role (test setup only)."""
    with engine.begin() as c:
        wid = c.execute(text("INSERT INTO workspaces (slug, name) VALUES ('other', 'Other') RETURNING id")).scalar_one()
        hpk = c.execute(text("INSERT INTO hosts (workspace_id, host_id, kind, created_by) VALUES (:w, 'foreign-01', 'live', 'e2e') RETURNING id"),
                        {"w": wid}).scalar_one()
        cid = c.execute(text("INSERT INTO clusters (workspace_id, name, created_by) VALUES (:w, 'theirs', 'e2e') RETURNING id"),
                        {"w": wid}).scalar_one()
        sid = c.execute(text("INSERT INTO services (workspace_id, name, created_by) VALUES (:w, 'theirs-svc', 'e2e') RETURNING id"),
                        {"w": wid}).scalar_one()
    return {"host_pk": hpk, "cluster": str(cid), "service": str(sid)}


async def test_another_workspaces_host_cluster_and_service_are_not_reachable(world, engine):
    other = _foreign_workspace(engine)
    mine = await make(world, "/api/v1/infrastructure/clusters", {"name": "mine"})
    svc = await make(world, "/api/v1/infrastructure/services", {"name": "mine-svc"})
    assert (await api(world, "POST", f"/api/v1/infrastructure/clusters/{mine['id']}/hosts", {"host_id": "foreign-01"})).status_code == 404
    assert (await api(world, "POST", f"/api/v1/infrastructure/services/{svc['id']}/hosts", {"host_id": "foreign-01"})).status_code == 404
    assert (await api(world, "PATCH", f"/api/v1/infrastructure/clusters/{other['cluster']}", {"name": "pwned"})).status_code == 404
    assert (await api(world, "DELETE", f"/api/v1/infrastructure/services/{other['service']}")).status_code == 404
    assert (await api(world, "POST", "/api/v1/infrastructure/regions", {"name": "r", "environment_id": str(uuid.uuid4())})).status_code == 404
    inv = await inventory(world)
    listed = str(inv)
    assert "theirs" not in listed and "foreign-01" not in listed


async def test_the_database_refuses_a_cross_workspace_host_reference_even_if_the_application_tried(world, engine, app_engine):
    other = _foreign_workspace(engine)
    await provision(world, host_id="mine-01")
    with pytest.raises(DBAPIError):
        with app_engine.begin() as c:
            c.execute(text("UPDATE hosts SET cluster_id = :c WHERE host_id = 'mine-01'"), {"c": other["cluster"]})
    with engine.connect() as c:
        assert c.execute(text("SELECT cluster_id FROM hosts WHERE host_id = 'mine-01'")).scalar_one() is None


async def test_the_application_role_cannot_change_host_fields_beyond_its_grant(world, app_engine):
    await provision(world, host_id="mine-01")
    with pytest.raises(DBAPIError, match="permission denied"):
        with app_engine.begin() as c:
            c.execute(text("UPDATE hosts SET kind = 'replay' WHERE host_id = 'mine-01'"))
    with pytest.raises(DBAPIError, match="permission denied"):
        with app_engine.begin() as c:
            c.execute(text("DELETE FROM hosts WHERE host_id = 'mine-01'"))


async def test_inventory_endpoints_require_a_browser_session(world):
    assert (await world["client"].get("/api/v1/infrastructure")).status_code == 401
    assert (await world["client"].get("/api/v1/infrastructure", headers=bearer(sub=OTHER_USER))).status_code == 403
    assert (await world["client"].post("/api/v1/infrastructure/environments", json={"name": "x"}, headers=bearer(sub=OTHER_USER))).status_code == 403
