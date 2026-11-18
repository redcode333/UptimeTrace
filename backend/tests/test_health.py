import pytest

from app.db import make_engine
from app.main import create_app
from tests.conftest import TEST_CREDENTIALS, make_client

pytestmark = pytest.mark.anyio


async def test_healthy_reports_app_and_database_separately(client):
    r = await client.get("/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok", "app": "ok", "database": "ok"}


async def test_database_down_is_503_with_app_still_ok_and_no_leak():
    # Nothing listens on port 1: a real connection failure, not a mock.
    dead = make_engine("postgresql://postgres:s3cretpw@127.0.0.1:1/nope", connect_timeout=2)
    async with make_client(create_app(dead, TEST_CREDENTIALS)) as client:
        r = await client.get("/health")
    assert r.status_code == 503
    assert r.json() == {"status": "degraded", "app": "ok", "database": "unavailable"}
    assert "s3cretpw" not in r.text and "127.0.0.1" not in r.text
