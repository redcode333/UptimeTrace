"""One-off: enable the least-privilege runtime role and switch backend/.env to it.

    python -m app.provision_app_role

Run it AFTER `alembic upgrade head` (migration 0002 creates the role as NOLOGIN, without a password)
and with an owner-level DATABASE_URL (or MIGRATION_DATABASE_URL) in backend/.env. It then
  1. generates a random password and enables LOGIN for `uptimetrace_app`,
  2. proves the new credential can log in,
  3. rewrites backend/.env:  MIGRATION_DATABASE_URL = the owner URL,  DATABASE_URL = the app-role URL.
Nothing secret is printed. Re-running rotates the app role's password.
"""
from __future__ import annotations

import os
import secrets
import shutil
import sys
import tempfile
from datetime import datetime
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.engine import make_url

from app.config import BACKEND_DIR, to_sqlalchemy_url
from app.db import make_engine

APP_ROLE = "uptimetrace_app"


def backup_env(env_path: Path) -> Path | None:
    """Copy .env aside (same directory, matched by the `.env.*` git-ignore rule) before rewriting it."""
    if not env_path.exists():
        return None
    backup = env_path.with_name(f"{env_path.name}.bak-{datetime.now():%Y%m%d%H%M%S}")
    shutil.copy2(env_path, backup)
    return backup


def _set_env_values(env_path: Path, values: dict[str, str]) -> None:
    lines = env_path.read_text(encoding="utf-8").splitlines() if env_path.exists() else []
    remaining = dict(values)
    out = []
    for line in lines:
        key = line.partition("=")[0].strip()
        out.append(f"{key}={remaining.pop(key)}" if key in remaining else line)
    out.extend(f"{k}={v}" for k, v in remaining.items())
    fd, tmp = tempfile.mkstemp(dir=env_path.parent, prefix=".env.", suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
        handle.write("\n".join(out) + "\n")
    os.replace(tmp, env_path)


def provision(owner_url: str, env_path: Path) -> Path | None:
    """Returns the path of the .env backup (or None if there was no .env)."""
    password = secrets.token_urlsafe(24)  # [A-Za-z0-9_-]: safe inside a SQL literal and a URL
    owner_engine = make_engine(owner_url)
    try:
        with owner_engine.begin() as connection:
            exists = connection.execute(text("SELECT 1 FROM pg_roles WHERE rolname = :r"), {"r": APP_ROLE}).scalar()
            if not exists:
                raise RuntimeError(f"role {APP_ROLE} does not exist: run `alembic upgrade head` first")
            connection.execute(text(f"ALTER ROLE {APP_ROLE} LOGIN PASSWORD '{password}'"))
    finally:
        owner_engine.dispose()

    app_url = make_url(to_sqlalchemy_url(owner_url)).set(username=APP_ROLE, password=password)
    app_url_text = app_url.set(drivername="postgresql").render_as_string(hide_password=False)
    probe = make_engine(app_url_text)
    try:
        with probe.connect() as connection:
            if connection.execute(text("SELECT current_user")).scalar_one() != APP_ROLE:
                raise RuntimeError("login verification returned an unexpected role")
    finally:
        probe.dispose()

    backup = backup_env(env_path)
    _set_env_values(env_path, {"MIGRATION_DATABASE_URL": owner_url, "DATABASE_URL": app_url_text})
    return backup


def main() -> int:
    from app.config import get_settings

    settings = get_settings()
    owner = settings.migration_database_url or settings.database_url
    try:
        backup = provision(owner.get_secret_value(), BACKEND_DIR / ".env")
    except Exception as exc:  # never print exc: driver messages can contain host details
        print(f"provisioning FAILED ({type(exc).__name__}); backend/.env was not changed")
        return 1
    print(f"{APP_ROLE}: login enabled and verified")
    print("backend/.env updated: DATABASE_URL -> app role, MIGRATION_DATABASE_URL -> owner")
    if backup:
        print(f"previous .env saved as {backup.name} (contains the old secrets: delete it once verified)")
    print("Verify with: python -m app.check_db   and   python -m app.check_db --migration")
    return 0


if __name__ == "__main__":
    sys.exit(main())
