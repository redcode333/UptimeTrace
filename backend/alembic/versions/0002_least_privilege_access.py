"""least-privilege access model for telemetry_envelopes

Revision ID: 0002
Revises: 0001

Why RLS alone is not enough on Supabase: new tables in `public` automatically receive
ALL privileges for anon / authenticated / service_role through default privileges, and the
Data API (PostgREST) serves them. RLS without policies hides rows from anon/authenticated,
but (a) service_role bypasses RLS entirely and (b) any later `CREATE POLICY` or
`DISABLE ROW LEVEL SECURITY` would silently expose the table. So this migration removes the
grants themselves (layer 1), keeps RLS enabled with policies naming only the backend role
(layer 2), and gives the backend its own non-owner role with the minimum privileges (layer 3).

The role is created NOLOGIN and WITHOUT a password: secrets never live in migrations.
An operator enables it once:  ALTER ROLE uptimetrace_app LOGIN PASSWORD '<generated>';
"""
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None

PUBLIC_API_ROLES = "('anon', 'authenticated', 'service_role')"


def upgrade() -> None:
    # --- the backend's dedicated role: not owner, not superuser, subject to RLS ---------------
    op.execute("""
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'uptimetrace_app') THEN
                CREATE ROLE uptimetrace_app NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE
                    NOREPLICATION NOBYPASSRLS;
            END IF;
        END
        $$
    """)

    # --- layer 1: remove every grant the Supabase Data API roles got by default ---------------
    op.execute("REVOKE ALL ON TABLE public.telemetry_envelopes FROM PUBLIC")
    op.execute(f"""
        DO $$
        DECLARE
            r text;
            seq text := pg_get_serial_sequence('public.telemetry_envelopes', 'id');
        BEGIN
            EXECUTE format('REVOKE ALL ON SEQUENCE %s FROM PUBLIC', seq);
            FOR r IN SELECT rolname FROM pg_roles WHERE rolname IN {PUBLIC_API_ROLES} LOOP
                EXECUTE format('REVOKE ALL ON TABLE public.telemetry_envelopes FROM %I', r);
                EXECUTE format('REVOKE ALL ON SEQUENCE %s FROM %I', seq, r);
                -- Alembic's own bookkeeping table is also in `public` and gets the same default grants.
                EXECUTE format('REVOKE ALL ON TABLE public.alembic_version FROM %I', r);
                -- Tables the migrating role creates in the future must not be exposed by default either.
                EXECUTE format('ALTER DEFAULT PRIVILEGES IN SCHEMA public REVOKE ALL ON TABLES FROM %I', r);
                EXECUTE format('ALTER DEFAULT PRIVILEGES IN SCHEMA public REVOKE ALL ON SEQUENCES FROM %I', r);
            END LOOP;
        END
        $$
    """)

    # --- layer 2: RLS stays on; the only policies belong to the backend role ------------------
    op.execute("ALTER TABLE public.telemetry_envelopes ENABLE ROW LEVEL SECURITY")
    op.execute("""
        CREATE POLICY telemetry_app_insert ON public.telemetry_envelopes
            FOR INSERT TO uptimetrace_app WITH CHECK (true)
    """)
    op.execute("""
        CREATE POLICY telemetry_app_select ON public.telemetry_envelopes
            FOR SELECT TO uptimetrace_app USING (true)
    """)

    # --- layer 3: least privilege for the backend --------------------------------------------
    # INSERT .. ON CONFLICT DO NOTHING RETURNING and the idempotency SELECT need INSERT + SELECT.
    # No UPDATE / DELETE / TRUNCATE / DDL. The identity sequence needs no separate grant.
    # /health runs `SELECT 1`, which needs nothing.
    op.execute("GRANT USAGE ON SCHEMA public TO uptimetrace_app")
    op.execute("GRANT SELECT, INSERT ON TABLE public.telemetry_envelopes TO uptimetrace_app")


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS telemetry_app_select ON public.telemetry_envelopes")
    op.execute("DROP POLICY IF EXISTS telemetry_app_insert ON public.telemetry_envelopes")
    op.execute("REVOKE ALL ON TABLE public.telemetry_envelopes FROM uptimetrace_app")
    # The role itself is cluster-level and may own a password / sessions: left in place on purpose.
    # Grants for anon/authenticated/service_role are deliberately NOT restored.
