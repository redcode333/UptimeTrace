"""Test harness: a REAL PostgreSQL (throwaway Docker instance), never the Supabase database."""
from __future__ import annotations

import os
import secrets
from datetime import UTC, datetime
from urllib.parse import urlsplit

import httpx
import pytest
from alembic import command
from alembic.config import Config
from dotenv import dotenv_values
from sqlalchemy import text
from sqlalchemy.engine import make_url

from app.auth import CredentialStore
from app.config import BACKEND_DIR
from app.db import make_engine
from app.main import create_app

FIXED_NOW = datetime(2026, 11, 11, 12, 0, 0, tzinfo=UTC)
# Ingestion requires a bearer token. The existing contract tests use an unrestricted TEST credential (any agent/host);
# scope enforcement is tested separately in tests/test_auth.py.
TEST_TOKEN = "utk_test_token_for_automated_tests_only_0123456789"
TEST_CREDENTIALS = CredentialStore.for_tests(TEST_TOKEN)


def _read(name: str) -> str | None:
    return os.environ.get(name) or dotenv_values(BACKEND_DIR / ".env").get(name)


def _safe_test_url() -> str:
    url = _read("TEST_DATABASE_URL")
    if not url:
        pytest.exit("TEST_DATABASE_URL is not set (see backend/.env.example). Refusing to run.", returncode=2)
    host = (urlsplit(url).hostname or "").lower()
    if url == _read("DATABASE_URL") or "supabase" in host or host not in {"localhost", "127.0.0.1", "::1"}:
        pytest.exit(
            "TEST_DATABASE_URL must point at a throwaway LOCAL PostgreSQL, never the dev database. "
            "Refusing to run destructive tests.",
            returncode=2,
        )
    return url


@pytest.fixture(scope="session")
def anyio_backend():
    return "asyncio"


@pytest.fixture(scope="session")
def test_url() -> str:
    return _safe_test_url()


def alembic_config(url: str) -> Config:
    config = Config(str(BACKEND_DIR / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND_DIR / "alembic"))
    config.attributes["url"] = url
    return config


@pytest.fixture(scope="session")
def engine(test_url):
    eng = make_engine(test_url)
    with eng.begin() as connection:  # fresh schema every run, then the real migrations
        connection.execute(text("DROP SCHEMA public CASCADE"))
        connection.execute(text("CREATE SCHEMA public"))
        # Reproduce Supabase: the Data API roles exist and every new public table is granted to them
        # by default. This is the trap the least-privilege migration has to defuse.
        for role in ("anon", "authenticated", "service_role"):
            connection.execute(text(
                f"DO $$ BEGIN IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = '{role}') "
                f"THEN CREATE ROLE {role} NOLOGIN; END IF; END $$"))
        connection.execute(text("GRANT USAGE ON SCHEMA public TO anon, authenticated, service_role"))
        connection.execute(text(
            "ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON TABLES TO anon, authenticated, service_role"))
        connection.execute(text(
            "ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON SEQUENCES TO anon, authenticated, service_role"))
    command.upgrade(alembic_config(test_url), "head")
    yield eng
    eng.dispose()


@pytest.fixture(scope="session")
def app_role_password() -> str:
    return secrets.token_hex(16)  # generated per run, never stored


@pytest.fixture(scope="session")
def app_engine(engine, test_url, app_role_password):
    """The application's real runtime identity: the least-privilege `uptimetrace_app` role."""
    password = app_role_password
    with engine.begin() as connection:
        connection.execute(text(f"ALTER ROLE uptimetrace_app LOGIN PASSWORD '{password}'"))
    url = make_url(test_url).set(username="uptimetrace_app", password=password)
    eng = make_engine(url.render_as_string(hide_password=False))
    yield eng
    eng.dispose()


@pytest.fixture(autouse=True)
def clean_table(request):
    """Empty the table before each test that uses the database."""
    if {"engine", "client", "app_engine"} & set(request.fixturenames):
        eng = request.getfixturevalue("engine")
        with eng.begin() as connection:
            connection.execute(text("TRUNCATE telemetry_envelopes RESTART IDENTITY"))
    yield


def make_client(app) -> httpx.AsyncClient:
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    return httpx.AsyncClient(transport=transport, base_url="http://test", headers={"Authorization": f"Bearer {TEST_TOKEN}"})


@pytest.fixture
def client(app_engine):
    return make_client(create_app(app_engine, TEST_CREDENTIALS, clock=lambda: FIXED_NOW))


@pytest.fixture
def count_rows(engine):
    def _count() -> int:
        with engine.connect() as connection:
            return connection.execute(text("SELECT count(*) FROM telemetry_envelopes")).scalar_one()

    return _count
