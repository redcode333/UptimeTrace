import pytest
from sqlalchemy import text

from app import provision_app_role
from app.db import make_engine


def test_provisioning_enables_the_app_role_and_rewrites_env_without_leaking(
        engine, test_url, tmp_path, capsys, app_role_password):
    env = tmp_path / ".env"
    env.write_text("OTHER=keep-me\nDATABASE_URL=old\n", encoding="utf-8")
    try:
        backup = provision_app_role.provision(test_url, env)
        assert backup is not None and backup.name.startswith(".env.bak-")
        assert backup.read_text(encoding="utf-8") == "OTHER=keep-me\nDATABASE_URL=old\n"  # original, byte for byte

        values = dict(line.split("=", 1) for line in env.read_text(encoding="utf-8").splitlines())
        assert values["OTHER"] == "keep-me"                       # unrelated settings preserved
        assert values["MIGRATION_DATABASE_URL"] == test_url       # owner kept for Alembic only
        assert "uptimetrace_app" in values["DATABASE_URL"] and values["DATABASE_URL"] != test_url

        app = make_engine(values["DATABASE_URL"])                 # the written URL really logs in
        with app.connect() as c:
            assert c.execute(text("SELECT current_user")).scalar_one() == "uptimetrace_app"
        app.dispose()
    finally:  # restore the session's shared password so other tests' pooled logins keep working
        with engine.begin() as c:
            c.execute(text(f"ALTER ROLE uptimetrace_app PASSWORD '{app_role_password}'"))
    out = capsys.readouterr()
    assert values["DATABASE_URL"].split(":")[2].split("@")[0] not in out.out + out.err


def test_provisioning_fails_cleanly_when_the_role_is_missing(engine, test_url, tmp_path, monkeypatch):
    env = tmp_path / ".env"
    env.write_text("DATABASE_URL=untouched\n", encoding="utf-8")
    with engine.begin() as c:
        c.execute(text("ALTER ROLE uptimetrace_app RENAME TO uptimetrace_app_x"))
    try:
        with pytest.raises(RuntimeError, match="alembic upgrade head"):
            provision_app_role.provision(test_url, env)
    finally:
        with engine.begin() as c:
            c.execute(text("ALTER ROLE uptimetrace_app_x RENAME TO uptimetrace_app"))
    assert env.read_text(encoding="utf-8") == "DATABASE_URL=untouched\n"
