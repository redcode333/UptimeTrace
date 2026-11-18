"""Access model: RLS is necessary but not sufficient, so grants, policies and the app role are all tested.

The test database reproduces Supabase's default privileges (anon/authenticated/service_role are granted
ALL on every new public table), so these tests fail if the migration only enabled RLS.
"""
import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

API_ROLES = ["anon", "authenticated", "service_role"]
INSERT = ("INSERT INTO telemetry_envelopes (envelope_id, agent_id, host_id, hostname, schema_version,"
          " envelope_timestamp, metric_count, content_hash, payload)"
          " VALUES (gen_random_uuid(), 'a', 'h', 'n', '1.0', now(), 1, 'x', '{}')")


def denied(engine, role, statement):
    """True if `statement` run as `role` fails with an insufficient-privilege error."""
    with engine.connect() as c:
        try:
            c.execute(text(f"SET LOCAL ROLE {role}"))
            c.execute(text(statement))
        except DBAPIError as exc:
            return "permission denied" in str(exc.orig).lower()
        finally:
            c.rollback()
    return False


@pytest.mark.parametrize("role", API_ROLES)
@pytest.mark.parametrize("statement", [
    "SELECT count(*) FROM telemetry_envelopes",
    INSERT,
    "UPDATE telemetry_envelopes SET hostname = 'x'",
    "DELETE FROM telemetry_envelopes",
    "TRUNCATE telemetry_envelopes",
    "SELECT * FROM alembic_version",
], ids=["select", "insert", "update", "delete", "truncate", "alembic_version"])
def test_supabase_data_api_roles_have_no_table_privileges(engine, role, statement):
    assert denied(engine, role, statement)


def test_without_grants_rls_policies_still_hide_every_row_from_api_roles(engine):
    """Second layer on its own: even if someone re-granted SELECT, no policy admits anon/authenticated."""
    with engine.begin() as c:
        c.execute(text(INSERT))
    with engine.connect() as c:
        c.execute(text("GRANT SELECT ON telemetry_envelopes TO anon, authenticated"))
        try:
            for role in ("anon", "authenticated"):
                c.execute(text(f"SET LOCAL ROLE {role}"))
                assert c.execute(text("SELECT count(*) FROM telemetry_envelopes")).scalar_one() == 0
                c.execute(text("RESET ROLE"))
        finally:
            c.rollback()  # the temporary GRANT is transactional and is undone


def test_only_the_backend_role_has_policies(engine):
    with engine.connect() as c:
        rows = c.execute(text(
            "SELECT policyname, cmd, roles FROM pg_policies WHERE tablename = 'telemetry_envelopes' ORDER BY policyname")).all()
        rls, forced = c.execute(text(
            "SELECT relrowsecurity, relforcerowsecurity FROM pg_class WHERE relname = 'telemetry_envelopes'")).one()
    assert rls is True
    assert [(r[0], r[1], list(r[2])) for r in rows] == [
        ("telemetry_app_insert", "INSERT", ["uptimetrace_app"]),
        ("telemetry_app_select", "SELECT", ["uptimetrace_app"]),
    ]


def test_backend_role_is_not_owner_superuser_or_rls_bypassing(engine):
    with engine.connect() as c:
        flags = c.execute(text(
            "SELECT rolsuper, rolbypassrls, rolcreaterole, rolcreatedb, rolreplication FROM pg_roles "
            "WHERE rolname = 'uptimetrace_app'")).one()
        owner = c.execute(text(
            "SELECT pg_get_userbyid(relowner) FROM pg_class WHERE relname = 'telemetry_envelopes'")).scalar_one()
    assert flags == (False, False, False, False, False)
    assert owner != "uptimetrace_app"


def test_backend_role_can_do_exactly_what_ingestion_needs(app_engine):
    with app_engine.begin() as c:
        c.execute(text(INSERT))
        assert c.execute(text("SELECT count(*) FROM telemetry_envelopes")).scalar_one() == 1
        c.execute(text("SELECT 1"))  # what /health runs
    with app_engine.connect() as c:
        assert c.execute(text("SELECT current_user")).scalar_one() == "uptimetrace_app"


@pytest.mark.parametrize("statement", [
    "UPDATE telemetry_envelopes SET hostname = 'x'",
    "DELETE FROM telemetry_envelopes",
    "TRUNCATE telemetry_envelopes",
    "DROP TABLE telemetry_envelopes",
    "CREATE TABLE public.sneaky (id int)",
    "ALTER TABLE telemetry_envelopes DISABLE ROW LEVEL SECURITY",
    "SELECT * FROM alembic_version",
], ids=["update", "delete", "truncate", "drop", "create-table", "disable-rls", "alembic_version"])
def test_backend_role_cannot_modify_delete_or_change_schema(app_engine, statement):
    with app_engine.connect() as c:
        with pytest.raises(DBAPIError) as info:
            c.execute(text(statement))
        c.rollback()
    assert "permission denied" in str(info.value.orig).lower() or "must be owner" in str(info.value.orig).lower()
