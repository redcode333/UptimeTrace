"""Envelope idempotency against real PostgreSQL."""
import asyncio

import pytest
from sqlalchemy import text

from tests.factories import make_envelope, post, raw

pytestmark = pytest.mark.anyio


def stored_payload(engine, envelope_id):
    with engine.connect() as c:
        return c.execute(text("SELECT payload FROM telemetry_envelopes WHERE envelope_id = :i"), {"i": envelope_id}).scalar_one()


async def test_exact_retry_returns_the_same_202_body_without_a_second_row(client, count_rows):
    envelope = make_envelope()
    first = await post(client, envelope)
    retry = await post(client, envelope)
    assert first.status_code == retry.status_code == 202
    assert first.json() == retry.json() == {
        "status": "accepted", "envelope_id": envelope["envelope_id"], "accepted_metrics": 3}
    assert count_rows() == 1


async def test_retry_with_different_json_key_order_is_equivalent(client, count_rows):
    envelope = make_envelope()
    first = await post(client, raw(envelope))
    retry = await post(client, raw(envelope, reverse_keys=True))
    assert raw(envelope) != raw(envelope, reverse_keys=True)  # the bytes really differ
    assert retry.status_code == 202 and retry.json() == first.json()
    assert count_rows() == 1


async def test_retry_writing_1_as_1_point_0_is_equivalent(client, count_rows):
    """Contract: parsed-JSON equality; 42 and 42.0 are 'the same kind of thing'."""
    envelope = make_envelope()
    first = await post(client, raw(envelope))
    assert b'"value": 42,' in raw(envelope)
    retry_bytes = raw(envelope).replace(b'"value": 42,', b'"value": 42.0,')
    assert retry_bytes != raw(envelope)
    retry = await post(client, retry_bytes)
    assert retry.status_code == 202 and retry.json() == first.json()
    assert count_rows() == 1


async def test_retry_writing_a_number_in_exponent_form_is_equivalent(client, count_rows):
    envelope = make_envelope()
    await post(client, raw(envelope))
    retry = await post(client, raw(envelope).replace(b'"value": 42,', b'"value": 4.2e1,'))
    assert retry.status_code == 202 and count_rows() == 1


@pytest.mark.parametrize("mutate", [
    lambda e: e["metrics"][0].update(value=42.6),              # one value differs
    lambda e: e.update(hostname="renamed-host"),                # envelope field differs
    lambda e: e["metrics"].append({**e["metrics"][2], "name": "app.extra.depth"}),  # extra observation
    lambda e: e.pop("service"),                                 # optional field dropped
    lambda e: e["metrics"].reverse(),                           # array order is part of the content
    lambda e: e["metrics"][0].update(timestamp="2026-11-11T11:59:58.5Z"),  # a different string for the same instant
], ids=["value", "hostname", "extra-metric", "service-dropped", "metric-order", "timestamp-spelling"])
async def test_same_id_with_different_content_is_409_and_original_is_untouched(client, engine, count_rows, mutate):
    original = make_envelope()
    assert (await post(client, original)).status_code == 202
    changed = make_envelope()
    changed["envelope_id"] = original["envelope_id"]
    changed["metrics"] = original["metrics"] and [dict(m) for m in original["metrics"]]
    changed["hostname"], changed["service"] = original["hostname"], original["service"]
    mutate(changed)

    r = await post(client, changed)

    assert r.status_code == 409
    assert r.json() == {"error": {
        "code": "envelope_id_conflict",
        "message": f"Envelope '{original['envelope_id']}' was already accepted with different content."}}
    assert count_rows() == 1
    assert stored_payload(engine, original["envelope_id"]) == original
    # the agent's genuine retry of the original still works after a conflict
    assert (await post(client, original)).status_code == 202


async def test_different_envelope_ids_with_identical_observations_are_both_stored(client, count_rows):
    """Contract: re-batched data is a NEW envelope; only envelope_id deduplicates."""
    a, b = make_envelope(), make_envelope()
    b["metrics"] = a["metrics"]
    assert (await post(client, a)).status_code == (await post(client, b)).status_code == 202
    assert count_rows() == 2


async def test_concurrent_identical_submissions_store_exactly_one_row(client, count_rows):
    envelope = make_envelope()
    responses = await asyncio.gather(*(post(client, envelope) for _ in range(25)))
    assert {r.status_code for r in responses} == {202}
    assert len({r.text for r in responses}) == 1
    assert count_rows() == 1


async def test_concurrent_equivalent_spellings_store_exactly_one_row(client, count_rows):
    envelope = make_envelope()
    bodies = [raw(envelope), raw(envelope, reverse_keys=True),
              raw(envelope).replace(b'"value": 42,', b'"value": 42.0,')] * 6
    responses = await asyncio.gather(*(post(client, b) for b in bodies))
    assert {r.status_code for r in responses} == {202}
    assert count_rows() == 1


async def test_concurrent_conflicting_submissions_have_one_winner_and_consistent_answers(client, engine, count_rows):
    a = make_envelope()
    b = make_envelope()
    b["envelope_id"] = a["envelope_id"]          # same id, different content (new default uuid replaced)
    b["hostname"] = "other-host"
    submissions = [a, b] * 10
    responses = await asyncio.gather(*(post(client, s) for s in submissions))

    assert count_rows() == 1
    winner = a if stored_payload(engine, a["envelope_id"]) == a else b
    assert stored_payload(engine, a["envelope_id"]) == winner
    for submitted, response in zip(submissions, responses):
        expected = 202 if submitted is winner else 409
        assert response.status_code == expected, (submitted["hostname"], response.text)


async def test_concurrent_distinct_envelopes_are_all_stored(client, count_rows):
    envelopes = [make_envelope() for _ in range(20)]
    responses = await asyncio.gather(*(post(client, e) for e in envelopes))
    assert {r.status_code for r in responses} == {202}
    assert count_rows() == 20
