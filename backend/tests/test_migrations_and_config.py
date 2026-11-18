import pytest
from alembic import command
from sqlalchemy import text

from app.config import Settings, to_sqlalchemy_url
from app.db import make_engine
from tests.conftest import alembic_config


def test_migration_creates_the_expected_schema(engine):
    with engine.connect() as c:
        cols = {r[0]: (r[1], r[2]) for r in c.execute(text(
            "SELECT column_name, data_type, is_nullable FROM information_schema.columns "
            "WHERE table_name = 'telemetry_envelopes'"))}
        unique = c.execute(text(
            "SELECT count(*) FROM pg_constraint WHERE conname = 'uq_telemetry_envelopes_envelope_id' AND contype = 'u'")).scalar_one()
        rls = c.execute(text("SELECT relrowsecurity FROM pg_class WHERE relname = 'telemetry_envelopes'")).scalar_one()
    assert cols["envelope_id"] == ("uuid", "NO")
    assert cols["payload"] == ("jsonb", "NO")
    assert cols["envelope_timestamp"] == ("timestamp with time zone", "NO")
    assert cols["received_at"] == ("timestamp with time zone", "NO")
    assert cols["service"] == ("character varying", "YES")
    assert unique == 1
    assert rls is True


def test_migration_downgrades_and_upgrades_again(engine, test_url):
    config = alembic_config(test_url)
    command.downgrade(config, "base")
    with engine.connect() as c:
        assert c.execute(text("SELECT to_regclass('telemetry_envelopes')")).scalar_one() is None
    command.upgrade(config, "head")
    with engine.connect() as c:
        assert c.execute(text("SELECT to_regclass('telemetry_envelopes')")).scalar_one() is not None


def test_settings_never_reveal_the_database_password():
    s = Settings(database_url="postgresql://postgres:hunter2-pw@db.example.supabase.co:5432/postgres")
    assert "hunter2-pw" not in repr(s) and "hunter2-pw" not in str(s) and "hunter2-pw" not in repr(s.model_dump())


def test_percent_encoded_password_round_trips_to_the_driver():
    """Regression for the unescaped-password URL that originally could not be parsed."""
    engine = make_engine("postgresql://postgres:p%40ss%2Fw%3Ard@db.example.supabase.co:5432/postgres")
    assert engine.url.password == "p@ss/w:rd"
    assert engine.url.host == "db.example.supabase.co" and engine.url.port == 5432
    assert engine.url.drivername == "postgresql+psycopg"
    assert "p@ss" not in repr(engine.url)  # SQLAlchemy masks the password in repr/str


@pytest.mark.parametrize("given, expected", [
    ("postgresql://u:p@h/d", "postgresql+psycopg://u:p@h/d"),
    ("postgres://u:p@h/d", "postgresql+psycopg://u:p@h/d"),
    ("postgresql+psycopg://u:p@h/d", "postgresql+psycopg://u:p@h/d"),
])
def test_driver_selection(given, expected):
    assert to_sqlalchemy_url(given) == expected
