"""Durable persistence, DB-level guarantees, and failure behaviour (real PostgreSQL)."""
import re
import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from app.db import make_engine
from app.main import create_app
from tests.conftest import FIXED_NOW, TEST_CREDENTIALS, make_client
from tests.factories import make_envelope, post

pytestmark = pytest.mark.anyio


async def test_accepted_envelope_is_committed_and_fully_stored(client, engine):
    envelope = make_envelope()
    envelope["metrics"][0]["value"] = 0.1          # a float that must survive unchanged
    envelope["metrics"][2]["value"] = -3.5e-3
    r = await post(client, envelope)
    assert r.status_code == 202

    # A different connection sees the row => it is committed, not just pending in a session.
    with engine.connect() as c:
        row = c.execute(text("SELECT * FROM telemetry_envelopes")).mappings().one()
        assert c.execute(text("SELECT jsonb_typeof(payload) FROM telemetry_envelopes")).scalar_one() == "object"
    assert row["envelope_id"] == uuid.UUID(envelope["envelope_id"])
    assert (row["agent_id"], row["host_id"], row["hostname"]) == ("agent-test", "host-test", "test-host")
    assert row["schema_version"] == "1.0" and row["service"] == "checkout-api" and row["metric_count"] == 3
    assert row["envelope_timestamp"] == datetime(2026, 11, 11, 11, 59, 59, tzinfo=UTC)
    assert re.fullmatch(r"[0-9a-f]{64}", row["content_hash"])
    assert row["received_at"].tzinfo is not None and abs((datetime.now(UTC) - row["received_at"]).total_seconds()) < 60
    assert row["payload"] == envelope               # nothing lost, including labels and unit/kind


async def test_service_is_optional_and_stored_as_null(client, engine):
    envelope = make_envelope()
    del envelope["service"]
    assert (await post(client, envelope)).status_code == 202
    with engine.connect() as c:
        assert c.execute(text("SELECT service FROM telemetry_envelopes")).scalar_one() is None


def test_database_itself_enforces_envelope_id_uniqueness(engine):
    """The guarantee must not depend on application code."""
    insert = text(
        "INSERT INTO telemetry_envelopes (envelope_id, agent_id, host_id, hostname, schema_version,"
        " envelope_timestamp, metric_count, content_hash, payload) VALUES (:i, 'a', 'h', 'n', '1.0', now(), 1, 'x', '{}')"
    )
    i = str(uuid.uuid4())
    with engine.begin() as c:
        c.execute(insert, {"i": i})
    with pytest.raises(IntegrityError), engine.begin() as c:
        c.execute(insert, {"i": i})


async def test_failure_at_commit_is_never_acknowledged_and_leaves_nothing_behind(client, engine, count_rows):
    """A deferred constraint trigger makes COMMIT itself fail, after the INSERT statement succeeded."""
    with engine.begin() as c:
        c.execute(text(
            "CREATE FUNCTION fail_on_commit() RETURNS trigger LANGUAGE plpgsql AS "
            "$$ BEGIN RAISE EXCEPTION 'simulated commit failure'; END $$"))
        c.execute(text(
            "CREATE CONSTRAINT TRIGGER fail_commit AFTER INSERT ON telemetry_envelopes "
            "DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION fail_on_commit()"))
    envelope = make_envelope()
    try:
        r = await post(client, envelope)
        assert r.status_code == 503 and r.json()["error"]["code"] == "unavailable"
        assert "simulated" not in r.text and "202" not in str(r.status_code)
        assert count_rows() == 0
    finally:
        with engine.begin() as c:
            c.execute(text("DROP TRIGGER fail_commit ON telemetry_envelopes"))
            c.execute(text("DROP FUNCTION fail_on_commit()"))

    # The agent retries the same envelope_id once storage recovers; it is accepted exactly once.
    assert (await post(client, envelope)).status_code == 202
    assert count_rows() == 1


async def test_database_unreachable_is_503_and_never_202():
    dead = make_engine("postgresql://postgres:s3cretpw@127.0.0.1:1/nope", connect_timeout=2)
    async with make_client(create_app(dead, TEST_CREDENTIALS, clock=lambda: FIXED_NOW)) as client:
        r = await post(client, make_envelope())
    assert r.status_code == 503 and r.json()["error"]["code"] == "unavailable"
    assert "s3cretpw" not in r.text and "127.0.0.1" not in r.text


async def test_nul_character_is_rejected_422_because_postgres_cannot_store_it(client, count_rows):
    envelope = make_envelope(hostname="host\u0000name")
    r = await post(client, envelope)
    assert r.status_code == 422
    assert [d["path"] for d in r.json()["error"]["details"]] == ["hostname"]
    assert count_rows() == 0


@pytest.mark.parametrize("mutate, path", [
    (lambda e: e.update(hostname="host\u0000name"), "hostname"),
    (lambda e: e["metrics"][1]["labels"].update(mount="/da\u0000ta"), "metrics[1].labels.mount"),
    (lambda e: e["metrics"].append({"name": "app.note.depth", "kind": "gauge", "value": 1, "unit": "count",
                                    "timestamp": "2026-11-11T11:59:58.500Z", "labels": {"note": "a\u0000b"}}),
     "metrics[3].labels.note"),
], ids=["hostname", "nested-label-value", "appended-metric-label"])
async def test_nul_is_rejected_before_any_database_access(mutate, path):
    """PostgreSQL storage restriction: the 422 must come from validation, not from a failed write.

    The engine points at a port nothing listens on. If the request reached the database it would be a
    503; getting 422 proves the rejection happens before persistence is attempted."""
    dead = make_engine("postgresql://postgres:x@127.0.0.1:1/nope", connect_timeout=2)
    envelope = make_envelope()
    mutate(envelope)
    async with make_client(create_app(dead, TEST_CREDENTIALS, clock=lambda: FIXED_NOW)) as client:
        r = await post(client, envelope)
    assert r.status_code == 422
    details = r.json()["error"]["details"]
    assert [d["path"] for d in details] == [path]
    assert "PostgreSQL" in details[0]["message"]
