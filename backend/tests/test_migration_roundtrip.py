"""Migration safety: upgrade/downgrade/re-upgrade on a SEPARATE throwaway database in the loopback test cluster.

Proves, with a Supabase-like role setup (anon / authenticated / service_role + default grants):
  * 0003..0005 apply on top of data written under 0002, and that data survives;
  * downgrading to 0002 removes only the new objects and keeps telemetry and the app role's access to it;
  * up -> down -> up yields EXACTLY the schema (columns, indexes, constraints, policies, privileges) of a fresh upgrade.
The guard is the same as conftest's: the cluster must be loopback and must not be the configured dev database.
"""
from __future__ import annotations

import uuid
from urllib.parse import urlsplit

import pytest
from alembic import command
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

from tests.conftest import alembic_config

NEW_TABLES = {"workspaces", "hosts", "agent_credentials", "audit_events", "mcp_tokens", "monitoring_settings", "alerts", "alert_events",
              "investigation_reports"}


def _admin_url(test_url: str):
    u = make_url(test_url)
    assert (urlsplit(test_url).hostname or "") in {"localhost", "127.0.0.1", "::1"}
    return u


def _prepare(admin_engine, name: str):
    with admin_engine.connect().execution_options(isolation_level="AUTOCOMMIT") as c:
        c.execute(text(f'DROP DATABASE IF EXISTS "{name}"'))
        c.execute(text(f'CREATE DATABASE "{name}"'))


def _supabase_like(engine):
    with engine.begin() as c:
        for role in ("anon", "authenticated", "service_role"):
            c.execute(text(f"DO $$ BEGIN IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = '{role}') THEN CREATE ROLE {role} NOLOGIN; END IF; END $$"))
        c.execute(text("GRANT USAGE ON SCHEMA public TO anon, authenticated, service_role"))
        c.execute(text("ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON TABLES TO anon, authenticated, service_role"))
        c.execute(text("ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON SEQUENCES TO anon, authenticated, service_role"))


def snapshot(engine) -> dict:
    q = {
        "columns": "SELECT table_name, column_name, data_type, is_nullable, column_default FROM information_schema.columns WHERE table_schema='public' ORDER BY 1,2",
        "indexes": "SELECT tablename, indexname, indexdef FROM pg_indexes WHERE schemaname='public' ORDER BY 1,2",
        "constraints": "SELECT conrelid::regclass::text, conname, pg_get_constraintdef(oid) FROM pg_constraint WHERE connamespace='public'::regnamespace ORDER BY 1,2",
        "policies": "SELECT tablename, policyname, cmd, roles::text, qual, with_check FROM pg_policies WHERE schemaname='public' ORDER BY 1,2",
        "rls": "SELECT relname, relrowsecurity FROM pg_class WHERE relnamespace='public'::regnamespace AND relkind='r' ORDER BY 1",
        "privileges": ("SELECT grantee, table_name, privilege_type FROM information_schema.role_table_grants WHERE table_schema='public' "
                       "AND grantee NOT IN ('postgres') ORDER BY 1,2,3"),
        "column_privileges": "SELECT grantee, table_name, column_name, privilege_type FROM information_schema.column_privileges WHERE table_schema='public' AND grantee IN ('uptimetrace_app','anon','authenticated','service_role','PUBLIC') ORDER BY 1,2,3,4",
    }
    with engine.connect() as c:
        return {k: [tuple(r) for r in c.execute(text(sql))] for k, sql in q.items()}


@pytest.fixture
def clusters(test_url):
    admin = create_engine(_admin_url(test_url).set(database="postgres").render_as_string(hide_password=False))
    names = {"a": "ut_mig_a", "b": "ut_mig_b"}
    engines = {}
    for k, n in names.items():
        _prepare(admin, n)
        url = _admin_url(test_url).set(database=n).render_as_string(hide_password=False)
        engines[k] = (create_engine(url), url)
        _supabase_like(engines[k][0])
    yield engines
    for e, _ in engines.values():
        e.dispose()
    with admin.connect().execution_options(isolation_level="AUTOCOMMIT") as c:
        for n in names.values():
            c.execute(text(f'DROP DATABASE IF EXISTS "{n}"'))
    admin.dispose()


def test_data_survives_upgrade_downgrade_and_schema_is_identical_to_a_fresh_upgrade(clusters):
    (eng, url), (fresh_eng, fresh_url) = clusters["a"], clusters["b"]
    cfg = alembic_config(url)

    command.upgrade(cfg, "0002")                                   # an existing deployment as it is today
    env_id = uuid.uuid4()
    with eng.begin() as c:
        c.execute(text("INSERT INTO telemetry_envelopes (envelope_id, agent_id, host_id, hostname, schema_version, envelope_timestamp, "
                       "metric_count, content_hash, payload) VALUES (:e,'a','h','h','1.0', now(), 1, repeat('0',64), '{}'::jsonb)"), {"e": env_id})
    before = snapshot(eng)

    command.upgrade(cfg, "head")
    with eng.connect() as c:
        tables = {r[0] for r in c.execute(text("SELECT tablename FROM pg_tables WHERE schemaname='public'"))}
        assert NEW_TABLES <= tables
        assert c.execute(text("SELECT count(*) FROM telemetry_envelopes WHERE envelope_id = :e"), {"e": env_id}).scalar_one() == 1
        assert c.execute(text("SELECT count(*) FROM workspaces WHERE slug = 'default'")).scalar_one() == 1      # seeded exactly once
        assert c.execute(text("SELECT version_num FROM alembic_version")).scalar_one() == "0007"   # current head (investigation evidence)
    # new tables carry registry data; the downgrade is documented to drop it
    with eng.begin() as c:
        ws = c.execute(text("SELECT id FROM workspaces WHERE slug='default'")).scalar_one()
        c.execute(text("INSERT INTO hosts (workspace_id, host_id, kind, created_by) VALUES (:w,'h','live','t')"), {"w": ws})

    command.downgrade(cfg, "0002")
    with eng.connect() as c:
        tables = {r[0] for r in c.execute(text("SELECT tablename FROM pg_tables WHERE schemaname='public'"))}
        assert not (NEW_TABLES & tables)                                                                       # new objects gone
        assert c.execute(text("SELECT count(*) FROM telemetry_envelopes WHERE envelope_id = :e"), {"e": env_id}).scalar_one() == 1  # data kept
        assert c.execute(text("SELECT version_num FROM alembic_version")).scalar_one() == "0002"
    assert snapshot(eng) == before                                                                             # downgrade restores the 0002 schema exactly

    command.upgrade(cfg, "head")                                                                               # and it can be re-applied
    command.upgrade(fresh_eng and alembic_config(fresh_url), "head")
    assert snapshot(eng) == snapshot(fresh_eng)                                                                # up/down/up == fresh


def test_no_supabase_api_role_can_touch_any_application_table_after_head(clusters):
    eng, url = clusters["a"]
    command.upgrade(alembic_config(url), "head")
    with eng.connect() as c:
        tables = [r[0] for r in c.execute(text("SELECT tablename FROM pg_tables WHERE schemaname='public' AND tablename <> 'alembic_version'"))]
        assert NEW_TABLES | {"telemetry_envelopes"} <= set(tables)
        for role in ("anon", "authenticated", "service_role"):
            for t in tables:
                assert not c.execute(text("SELECT has_table_privilege(:r, :t, 'SELECT,INSERT,UPDATE,DELETE,TRUNCATE')"), {"r": role, "t": t}).scalar_one(), (role, t)
        rls = dict(c.execute(text("SELECT relname, relrowsecurity FROM pg_class WHERE relnamespace='public'::regnamespace AND relkind='r'")).all())
        assert all(rls[t] for t in tables)
