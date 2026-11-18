"""Connectivity check that never prints credentials, hosts or URLs.

    python -m app.check_db              # runtime identity (DATABASE_URL)
    python -m app.check_db --migration  # migration/owner identity (MIGRATION_DATABASE_URL or DATABASE_URL)

Reports only: success/failure, the database role name, whether that role is a superuser / bypasses
RLS / owns the telemetry table, and whether it can read and insert (insert is rolled back).
"""
from __future__ import annotations

import sys

from sqlalchemy import text

from app.config import get_settings
from app.db import make_engine


def main(argv: list[str]) -> int:
    settings = get_settings()
    secret = settings.migration_database_url if "--migration" in argv and settings.migration_database_url else settings.database_url
    engine = make_engine(secret.get_secret_value(), settings.db_connect_timeout_seconds)
    try:
        with engine.connect() as c:
            role, superuser, bypass = c.execute(text(
                "SELECT current_user, r.rolsuper, r.rolbypassrls FROM pg_roles r WHERE r.rolname = current_user")).one()
            owner = c.execute(text(
                "SELECT pg_get_userbyid(relowner) = current_user FROM pg_class "
                "WHERE relname = 'telemetry_envelopes' AND relkind = 'r'")).scalar_one_or_none()
            can_read = c.execute(text("SELECT has_table_privilege('telemetry_envelopes', 'SELECT')")).scalar_one()
            can_insert = c.execute(text("SELECT has_table_privilege('telemetry_envelopes', 'INSERT')")).scalar_one()
            c.execute(text("SELECT 1"))
    except Exception as exc:  # never print exc: driver messages can contain host details
        print(f"connection FAILED ({type(exc).__name__})")
        return 1
    finally:
        engine.dispose()
    print("connection OK")
    print(f"role={role} superuser={superuser} bypass_rls={bypass} owns_table={owner} "
          f"can_select={can_read} can_insert={can_insert}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
