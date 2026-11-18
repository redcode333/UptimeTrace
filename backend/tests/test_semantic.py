"""Semantic rules from the contract README: unit tests for boundaries, then the HTTP mapping."""
from datetime import UTC, datetime, timedelta

import pytest

from app.ingest.semantic import (
    check_duplicate_observations,
    check_envelope_vs_server,
    check_observations_vs_envelope,
    validate_semantics,
)
from tests.conftest import FIXED_NOW
from tests.factories import make_envelope, post

pytestmark = pytest.mark.anyio


def obs(ts, name="app.queue.depth", labels=None, value=1):
    metric = {"name": name, "kind": "gauge", "value": value, "unit": "count", "timestamp": ts}
    if labels is not None:
        metric["labels"] = labels
    return metric


def envelope(ts="2026-11-11T11:00:00Z", *metrics):
    return {"timestamp": ts, "metrics": list(metrics)}


# ---- check 1: observation <= envelope + 5 s -------------------------------------------------

def test_observation_exactly_5s_after_envelope_is_allowed_and_one_microsecond_more_is_not():
    ok = envelope("2026-11-11T11:00:00Z", obs("2026-11-11T11:00:05Z"))
    bad = envelope("2026-11-11T11:00:00Z", obs("2026-11-11T11:00:05.000001Z"))
    assert check_observations_vs_envelope(ok) == []
    [error] = check_observations_vs_envelope(bad)
    assert error.path == "metrics[0].timestamp"


def test_observations_older_than_envelope_by_any_amount_are_allowed():
    assert check_observations_vs_envelope(envelope("2026-11-11T11:00:00Z", obs("2020-01-01T00:00:00Z"))) == []


def test_each_late_observation_is_reported_with_its_own_index():
    e = envelope("2026-11-11T11:00:00Z", obs("2026-11-11T10:59:00Z"), obs("2026-11-11T11:01:00Z"),
                 obs("2026-11-11T11:02:00Z", name="app.other.depth"))
    assert [d.path for d in check_observations_vs_envelope(e)] == ["metrics[1].timestamp", "metrics[2].timestamp"]


# ---- check 2: envelope <= server + 5 min ------------------------------------------------------

def test_envelope_exactly_5min_ahead_of_server_is_allowed_and_one_microsecond_more_is_not():
    now = datetime(2026, 11, 11, 12, 0, 0, tzinfo=UTC)
    assert check_envelope_vs_server({"timestamp": "2026-11-11T12:05:00Z"}, now) == []
    [error] = check_envelope_vs_server({"timestamp": "2026-11-11T12:05:00.000001Z"}, now)
    assert error.path == "timestamp"


def test_old_envelopes_have_no_lower_bound():
    assert check_envelope_vs_server({"timestamp": "1000-01-01T00:00:00Z"}, FIXED_NOW) == []


# ---- check 3: duplicates within an envelope -------------------------------------------------

def test_duplicate_is_the_second_occurrence_even_with_different_value_and_label_order():
    t = "2026-11-11T11:00:00Z"
    e = envelope(t, obs(t, labels={"a": "1", "b": "2"}, value=1), obs(t, labels={"b": "2", "a": "1"}, value=999))
    [error] = check_duplicate_observations(e)
    assert error.path == "metrics[1]"


def test_absent_labels_equal_empty_labels():
    t = "2026-11-11T11:00:00Z"
    assert len(check_duplicate_observations(envelope(t, obs(t), obs(t, labels={})))) == 1


def test_same_instant_written_differently_is_still_a_duplicate():
    e = envelope("2026-11-11T11:00:00Z", obs("2026-11-11T10:00:00Z"), obs("2026-11-11T10:00:00.000Z"))
    assert len(check_duplicate_observations(e)) == 1


@pytest.mark.parametrize("second", [
    obs("2026-11-11T10:00:01Z"),                        # later sample of the same series
    obs("2026-11-11T10:00:00Z", labels={"mount": "/"}),  # different label set
    obs("2026-11-11T10:00:00Z", name="app.other.depth"),  # different metric
])
def test_legitimate_repeats_are_not_duplicates(second):
    assert check_duplicate_observations(envelope("2026-11-11T11:00:00Z", obs("2026-11-11T10:00:00Z"), second)) == []


def test_checks_are_independent_and_all_reported_together():
    t = "2026-11-11T12:30:00Z"  # envelope 30 min ahead of FIXED_NOW
    e = envelope(t, obs("2026-11-11T12:31:00Z"), obs("2026-11-11T12:31:00Z"))
    assert {d.path for d in validate_semantics(e, FIXED_NOW)} == {"metrics[0].timestamp", "metrics[1].timestamp", "timestamp", "metrics[1]"}


# ---- through HTTP ---------------------------------------------------------------------------

async def test_semantic_failure_is_422_with_paths_and_nothing_is_stored(client, count_rows):
    e = make_envelope()
    e["metrics"].append(dict(e["metrics"][0]))                               # duplicate (index 3)
    e["metrics"][2]["timestamp"] = "2026-11-11T12:00:00Z"                     # 1 s after envelope ts -> allowed
    e["metrics"][1]["timestamp"] = "2026-11-11T12:00:30Z"                     # 31 s after -> check 1
    r = await post(client, e)
    assert r.status_code == 422
    assert {d["path"] for d in r.json()["error"]["details"]} == {"metrics[1].timestamp", "metrics[3]"}
    assert count_rows() == 0


async def test_envelope_too_far_in_server_future_is_422(client):
    future = (FIXED_NOW + timedelta(minutes=5, seconds=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
    r = await post(client, make_envelope(timestamp=future))
    assert r.status_code == 422
    assert [d["path"] for d in r.json()["error"]["details"]] == ["timestamp"]


async def test_buffered_replay_of_very_old_data_is_accepted(client):
    e = make_envelope(timestamp="2025-01-01T00:00:00Z")
    for m in e["metrics"]:
        m["timestamp"] = "2024-12-31T23:00:00Z"
    assert (await post(client, e)).status_code == 202
