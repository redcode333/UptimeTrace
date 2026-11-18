"""Consistent contract-shaped errors for routing failures, database failures and internal errors."""
import pytest
from sqlalchemy.exc import InvalidRequestError, OperationalError
from sqlalchemy.exc import TimeoutError as PoolTimeoutError

import app.main as main
from app.db import make_engine
from app.main import create_app
from tests.conftest import FIXED_NOW, TEST_CREDENTIALS, make_client
from tests.factories import make_envelope, post

pytestmark = pytest.mark.anyio

LEAKY = "INSERT INTO telemetry_envelopes VALUES (:p) password=hunter2 postgresql://u:hunter2@db.host/x"


def shape(response):
    body = response.json()
    assert set(body) == {"error"} and set(body["error"]) == {"code", "message"}, body
    assert "Traceback" not in response.text and "hunter2" not in response.text
    return body["error"]["code"]


async def test_unknown_path_is_404_in_contract_envelope(client):
    r = await client.get("/api/v1/nope")
    assert r.status_code == 404 and shape(r) == "not_found"


async def test_wrong_method_is_405_with_allow_header(client):
    r = await client.get("/api/v1/telemetry")
    assert r.status_code == 405 and shape(r) == "method_not_allowed"
    assert "POST" in r.headers["allow"]
    r = await client.post("/health")
    assert r.status_code == 405 and shape(r) == "method_not_allowed"


async def test_unexpected_application_error_is_500(client, monkeypatch):
    monkeypatch.setattr(main, "store_envelope", lambda *a, **k: (_ for _ in ()).throw(KeyError("hunter2")))
    r = await post(client, make_envelope())
    assert r.status_code == 500 and shape(r) == "internal_error"


@pytest.mark.parametrize("exc", [
    OperationalError(LEAKY, {}, Exception("connection refused")),
    PoolTimeoutError("QueuePool limit of size 5 overflow 10 reached " + LEAKY),
], ids=["operational", "pool-timeout"])
async def test_database_unavailability_is_503_without_leaking_sql(client, monkeypatch, exc):
    monkeypatch.setattr(main, "store_envelope", lambda *a, **k: (_ for _ in ()).throw(exc))
    r = await post(client, make_envelope())
    assert r.status_code == 503 and shape(r) == "unavailable"
    assert "INSERT" not in r.text and "postgresql://" not in r.text


async def test_sqlalchemy_misuse_is_an_application_bug_not_database_unavailability(client, monkeypatch):
    monkeypatch.setattr(main, "store_envelope", lambda *a, **k: (_ for _ in ()).throw(InvalidRequestError(LEAKY)))
    r = await post(client, make_envelope())
    assert r.status_code == 500 and shape(r) == "internal_error"


async def test_real_connection_failure_is_503_on_every_database_route():
    dead = make_engine("postgresql://postgres:hunter2@127.0.0.1:1/nope", connect_timeout=2)
    async with make_client(create_app(dead, TEST_CREDENTIALS, clock=lambda: FIXED_NOW)) as client:
        ingest = await post(client, make_envelope())
        health = await client.get("/health")
    assert ingest.status_code == 503 and shape(ingest) == "unavailable"
    assert health.status_code == 503 and "hunter2" not in health.text
