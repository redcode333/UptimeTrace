"""Failure rehearsals. Three layers:

1. Pure verdict rules (no database): execution and detection are separate, and a failed injection can never count as detected.
2. The real executor against real PostgreSQL: real ingest validation and storage, real alert evaluation, real recovery, read-back of
   stored telemetry. Only the clock and the waits are controlled, so every run is repeatable.
3. The browser API: authorisation, eligibility, confirmation, one-running-at-a-time, stop, and the database grants.

Nothing here runs a command on a machine. The synthetic host's telemetry is what is under test.
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, IntegrityError

from app.config import SCHEMA_PATH
from app.db import make_session_factory
from app.ingest.schema import EnvelopeSchemaValidator
from app.main import create_app
from app.models import FailureRehearsal
from app.monitoring import settings as cfg_store
from app.registry.store import RegistryCredentialStore
from app.rehearsal import catalogue as C
from app.rehearsal import evaluate as E
from app.rehearsal import runner as R
from app.rehearsal import service as S
from tests.clerk_support import bearer, verifier
from tests.conftest import FIXED_NOW
from tests.factories import make_envelope

SIM = "sim-rehearsal-01"
LIVE = "live-rehearsal-01"
CPU = "system.cpu.utilization"


# ------------------------------------------------------------------------------------------------------------- 1. pure rules
def run(**over) -> E.RunFacts:
    base = dict(planned=11, sent=11, accepted=11, fault_sent=6, fault_accepted=6, recovery_accepted=3, fault_confirmed=True,
                stop_requested=False, aborted=False, interrupted=False, cleanup="not_needed")
    base.update(over)
    return E.RunFacts(**base)


T = FIXED_NOW
NO_ALERT = E.AlertFacts(opened_at=None, resolved_at=None, resolution=None, preexisting_live=False)


def alert(delay=0.2, resolved=True, resolution="recovered", preexisting=False):
    opened = T + timedelta(seconds=delay)
    resolved_at = (T + timedelta(seconds=40)) if resolved else None
    return E.AlertFacts(opened_at=opened, resolved_at=resolved_at, resolution=resolution if resolved else None,
                        preexisting_live=preexisting)


def test_completed_and_detected_within_budget_with_recovery():
    v = E.evaluate(run(), fault_onset=T, recovery_sent_at=T + timedelta(seconds=30), alert=alert(delay=0.2))
    assert (v.execution, v.detection) == (E.EXEC_COMPLETED, E.DET_DETECTED)
    assert v.detection_delay_seconds == pytest.approx(0.2)
    assert v.recovery_observed is True and v.recovery_seconds == pytest.approx(10.0)


def test_a_late_alert_is_late_detection_not_detected():
    v = E.evaluate(run(), fault_onset=T, recovery_sent_at=T + timedelta(seconds=30), alert=alert(delay=C.DETECTION_BUDGET_SECONDS + 5))
    assert v.detection == E.DET_LATE and v.detection_delay_seconds > C.DETECTION_BUDGET_SECONDS


def test_fault_confirmed_with_no_alert_is_undetected_when_the_run_finished():
    v = E.evaluate(run(), fault_onset=T, recovery_sent_at=T, alert=NO_ALERT)
    assert (v.execution, v.detection) == (E.EXEC_COMPLETED, E.DET_UNDETECTED)
    assert v.detection_delay_seconds is None and v.recovery_observed is None


def test_failed_injection_is_failed_and_detection_is_inconclusive_even_if_an_alert_exists():
    run_failed = run(fault_confirmed=False, fault_accepted=0, accepted=5, recovery_accepted=0, aborted=True, cleanup="recovered")
    v = E.evaluate(run_failed, fault_onset=None, recovery_sent_at=T, alert=alert(delay=0.1))
    assert v.execution == E.EXEC_FAILED and "no fault telemetry" in v.execution_reason
    assert v.detection == E.DET_INCONCLUSIVE   # an alert cannot be credited to a fault that never happened


def test_fault_below_the_warning_limit_is_a_failed_injection_not_a_missed_detection():
    v = E.evaluate(run(fault_confirmed=False), fault_onset=None, recovery_sent_at=T, alert=NO_ALERT)
    assert v.execution == E.EXEC_FAILED and "never reached the CPU warning limit" in v.execution_reason
    assert v.detection == E.DET_INCONCLUSIVE


def test_stop_after_confirmed_fault_is_stopped_and_still_judges_detection():
    v = E.evaluate(run(stop_requested=True, sent=4, accepted=4, fault_sent=2, fault_accepted=2, recovery_accepted=1,
                       planned=11, cleanup="recovered"), fault_onset=T, recovery_sent_at=T, alert=alert(delay=0.1))
    assert v.execution == E.EXEC_STOPPED and v.detection == E.DET_DETECTED


def test_stop_before_the_fault_is_stopped_with_inconclusive_detection():
    v = E.evaluate(run(stop_requested=True, sent=1, accepted=1, fault_sent=0, fault_accepted=0, recovery_accepted=0,
                       fault_confirmed=False), fault_onset=None, recovery_sent_at=None, alert=NO_ALERT)
    assert v.execution == E.EXEC_STOPPED and v.detection == E.DET_INCONCLUSIVE


def test_interrupted_run_is_inconclusive():
    v = E.evaluate(run(interrupted=True, accepted=5, sent=5), fault_onset=T, recovery_sent_at=T, alert=alert(delay=0.1))
    assert v.execution == E.EXEC_INCONCLUSIVE and "interrupted" in v.execution_reason


def test_failed_cleanup_leaves_the_run_inconclusive():
    v = E.evaluate(run(cleanup="failed", recovery_accepted=0), fault_onset=T, recovery_sent_at=None, alert=alert(delay=0.1))
    assert v.execution == E.EXEC_INCONCLUSIVE and "may still report the fault" in v.execution_reason


def test_partly_accepted_plan_with_confirmed_fault_is_inconclusive_not_completed():
    v = E.evaluate(run(accepted=9), fault_onset=T, recovery_sent_at=T, alert=alert(delay=0.1))
    assert v.execution == E.EXEC_INCONCLUSIVE and "2 of 11" in v.execution_reason


def test_a_pre_existing_alert_cannot_be_credited_to_the_fault():
    pre = E.AlertFacts(opened_at=None, resolved_at=None, resolution=None, preexisting_live=True)
    v = E.evaluate(run(), fault_onset=T, recovery_sent_at=T, alert=pre)
    assert v.detection == E.DET_INCONCLUSIVE and "already open" in v.detection_reason


def test_recovery_resolved_by_an_operator_is_not_observed_recovery():
    v = E.evaluate(run(), fault_onset=T, recovery_sent_at=T, alert=alert(delay=0.1, resolution="manual"))
    assert v.recovery_observed is False


def test_unresolved_alert_at_the_end_means_recovery_not_observed():
    v = E.evaluate(run(), fault_onset=T, recovery_sent_at=T, alert=alert(delay=0.1, resolved=False))
    assert v.recovery_observed is False


# ------------------------------------------------------------------------------------ 2. the real executor, real PostgreSQL
class Clock:
    def __init__(self, at: datetime):
        self.at = at

    def __call__(self) -> datetime:
        return self.at

    def advance(self, seconds: float) -> None:
        self.at = self.at + timedelta(seconds=seconds)


@pytest.fixture(autouse=True)
def clean_rehearsals(engine):
    with engine.begin() as c:
        c.execute(text("TRUNCATE failure_rehearsal_steps, failure_rehearsals, alert_events, alerts, monitoring_settings, "
                       "telemetry_envelopes, agent_credentials, hosts, audit_events RESTART IDENTITY CASCADE"))
    yield


def _register(engine, host_id: str, kind: str) -> None:
    """Host rows are created through the owner role here (the registry API is covered by the API tests)."""
    with engine.begin() as c:
        wid = c.execute(text("SELECT id FROM workspaces WHERE slug = 'default'")).scalar_one()
        c.execute(text("INSERT INTO hosts (id, workspace_id, host_id, kind, created_by) VALUES (gen_random_uuid(), :w, :h, :k, 'test')"),
                  {"w": wid, "h": host_id, "k": kind})


def _ts(dt: datetime) -> str:
    return dt.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")[:-4] + "Z"


def _cpu_metric(value: float, when: datetime) -> dict:
    return {"name": CPU, "kind": "gauge", "value": value, "unit": "percent", "timestamp": _ts(when - timedelta(seconds=0.5))}


def _execute(app_engine, clock: Clock, host_id: str = SIM, wrap=None, stop_at: int | None = None,
             scenario: str = "cpu_saturation") -> FailureRehearsal:
    """Start a rehearsal and run its plan synchronously. `wait` advances the test clock like a real 15 s cadence would."""
    sf = make_session_factory(app_engine)
    submit = S.make_submit(sf, EnvelopeSchemaValidator(SCHEMA_PATH), clock)
    if wrap is not None:
        submit = wrap(submit)
    rid = S.start(sf, "operator-test", host_id, scenario, clock())
    ctx = S._context(sf, rid, submit, clock, event=None)
    waits = {"n": 0}

    def wait(seconds: float) -> bool:
        waits["n"] += 1
        clock.advance(seconds)
        if stop_at is not None and waits["n"] == stop_at:
            S.request_stop(sf, rid, "operator-test", clock())
        return False

    ctx.wait = wait
    R.execute(ctx)
    with sf() as s:
        row = S.get(s, rid)
        s.expunge(row)
        return row


def _steps(app_engine, rid) -> list[tuple]:
    with app_engine.connect() as c:
        return [tuple(r) for r in c.execute(text("SELECT seq, phase, outcome, error_class FROM failure_rehearsal_steps "
                                                  "WHERE rehearsal_id = :r ORDER BY seq"), {"r": rid})]


def test_happy_path_injects_detects_recovers_and_records_the_delay(app_engine, engine):
    _register(engine, SIM, "synthetic")
    row = _execute(app_engine, Clock(FIXED_NOW))
    assert row.execution == E.EXEC_COMPLETED, row.execution_reason
    assert row.detection == E.DET_DETECTED, row.detection_reason
    assert row.fault_confirmed_at is not None and row.fault_observed_value >= 92.0          # read back from stored telemetry
    assert 0.0 <= row.detection_delay_seconds <= C.DETECTION_BUDGET_SECONDS
    assert row.recovery_observed is True and row.recovery_seconds is not None
    assert row.alert_id is not None and row.evidence_useful is True and row.evidence_points > 0
    assert row.cleanup_status == "not_needed"
    assert row.accepted_envelopes == row.planned_envelopes == 11 and row.failed_envelopes == 0
    steps = _steps(app_engine, row.id)
    assert [p for _, p, _, _ in steps].count("fault") == 6 and len(steps) == 11
    with app_engine.connect() as c:
        state, resolution = c.execute(text("SELECT state, resolution FROM alerts WHERE id = :a"), {"a": row.alert_id}).one()
    assert (state, resolution) == ("resolved", "recovered")


def test_failed_injection_is_recorded_as_failed_and_never_as_detected(app_engine, engine):
    _register(engine, SIM, "synthetic")

    def store_down_for_fault(inner):
        def submit(doc):
            if any(m["name"] == CPU and m["value"] >= 92 for m in doc["metrics"]):
                raise RuntimeError("telemetry store unavailable")
            return inner(doc)
        return submit

    row = _execute(app_engine, Clock(FIXED_NOW), wrap=store_down_for_fault)
    assert row.execution == E.EXEC_FAILED and "no fault telemetry" in row.execution_reason
    assert row.detection == E.DET_INCONCLUSIVE and row.fault_confirmed_at is None
    assert row.alert_id is None and row.cleanup_status == "recovered"
    failed = [s for s in _steps(app_engine, row.id) if s[2] == "failed"]
    assert len(failed) == C.CONSECUTIVE_FAILURE_LIMIT and all(s[3] == "RuntimeError" for s in failed)


def test_fault_below_the_warning_limit_is_failed_injection(app_engine, engine):
    _register(engine, SIM, "synthetic")

    def fault_does_not_reach_the_threshold(inner):
        def submit(doc):
            for m in doc["metrics"]:
                if m["name"] == CPU and m["value"] >= 92:
                    m["value"] = 30.0
            return inner(doc)
        return submit

    row = _execute(app_engine, Clock(FIXED_NOW), wrap=fault_does_not_reach_the_threshold)
    assert row.execution == E.EXEC_FAILED and "never reached the CPU warning limit" in row.execution_reason
    assert row.detection == E.DET_INCONCLUSIVE and row.alert_id is None
    assert row.fault_accepted == 6 and row.fault_confirmed_at is None


def test_missing_detection_is_undetected_when_alerting_does_not_open_an_alert(app_engine, engine, monkeypatch):
    _register(engine, SIM, "synthetic")
    monkeypatch.setattr(S, "evaluate_after_ingest", lambda *a, **k: None)   # the alert path is silent for this run
    row = _execute(app_engine, Clock(FIXED_NOW))
    assert row.execution == E.EXEC_COMPLETED
    assert row.fault_confirmed_at is not None                                # the fault really was in stored telemetry
    assert row.detection == E.DET_UNDETECTED and row.alert_id is None and row.recovery_observed is None


def test_manual_stop_during_the_fault_ends_the_fault_and_records_stopped(app_engine, engine):
    _register(engine, SIM, "synthetic")
    row = _execute(app_engine, Clock(FIXED_NOW), stop_at=4)               # stop after the fourth envelope, during the fault
    assert row.execution == E.EXEC_STOPPED, row.execution_reason
    assert row.stop_requested_by == "operator-test" and row.stop_requested_at is not None
    assert row.fault_sent == 2 and row.cleanup_status == "recovered"
    assert row.detection == E.DET_DETECTED and row.recovery_observed is True
    assert any(p == "cleanup" and o == "accepted" for _, p, o, _ in _steps(app_engine, row.id))


def test_stop_before_the_fault_starts_is_stopped_with_inconclusive_detection(app_engine, engine):
    _register(engine, SIM, "synthetic")
    row = _execute(app_engine, Clock(FIXED_NOW), stop_at=1)
    assert row.execution == E.EXEC_STOPPED and row.fault_sent == 0
    assert row.detection == E.DET_INCONCLUSIVE and row.cleanup_status == "not_needed"


def test_preexisting_cpu_alert_cannot_be_credited_to_the_rehearsal(app_engine, engine):
    """An independent CPU alert is live when the rehearsal starts, and the rehearsal's baseline cannot clear it (its first two
    envelopes are not accepted). The fault then reaches the same condition, which opens no NEW alert, so the detection is
    INCONCLUSIVE rather than a credited DETECTED."""
    _register(engine, SIM, "synthetic")
    sf = make_session_factory(app_engine)
    before = Clock(FIXED_NOW - timedelta(seconds=30))
    submit = S.make_submit(sf, EnvelopeSchemaValidator(SCHEMA_PATH), before)
    when = before()
    submit(make_envelope(host_id=SIM, agent_id="sim-rehearsal-agent", hostname="sim.local", timestamp=_ts(when),
                         metrics=[_cpu_metric(97.0, when)]))
    with sf() as s:
        assert s.execute(text("SELECT count(*) FROM alerts WHERE state = 'open'")).scalar_one() == 1

    def baseline_not_stored(inner):
        seen = {"low": 0}

        def submit_(doc):
            cpu = next(m["value"] for m in doc["metrics"] if m["name"] == CPU)
            if cpu < 50 and seen["low"] < 2:
                seen["low"] += 1
                raise RuntimeError("store busy")
            return inner(doc)
        return submit_

    row = _execute(app_engine, Clock(FIXED_NOW), wrap=baseline_not_stored)
    assert row.fault_confirmed_at is not None
    assert row.detection == E.DET_INCONCLUSIVE and "already open" in row.detection_reason
    assert row.execution == E.EXEC_INCONCLUSIVE and "2 of 11" in row.execution_reason


def test_interrupted_rehearsal_is_judged_inconclusive_and_its_fault_is_ended(app_engine, engine):
    _register(engine, SIM, "synthetic")
    sf = make_session_factory(app_engine)
    clock = Clock(FIXED_NOW)
    with sf() as s, s.begin():
        rid = S.start(sf, "operator-test", SIM, "cpu_saturation", FIXED_NOW - timedelta(minutes=10))
    with engine.begin() as c:   # what a crash mid-fault leaves behind: three fault envelopes accepted, no recovery
        c.execute(text("UPDATE failure_rehearsals SET fault_sent = 3, fault_accepted = 3, sent_envelopes = 5, accepted_envelopes = 5, "
                       "fault_confirmed_at = :f, fault_observed_value = 96.0, last_progress_at = :p, phase = 'fault' WHERE id = :r"),
                  {"f": FIXED_NOW - timedelta(minutes=10), "p": FIXED_NOW - timedelta(minutes=10), "r": rid})
    judged = S.reconcile_interrupted(sf, S.make_submit(sf, EnvelopeSchemaValidator(SCHEMA_PATH), clock), clock)
    assert judged == 1
    with sf() as s:
        row = S.get(s, rid)
    assert row.execution == E.EXEC_INCONCLUSIVE and "interrupted" in row.execution_reason
    assert row.cleanup_status == "recovered" and row.ended_at is not None
    assert row.detection == E.DET_INCONCLUSIVE


def test_a_fresh_rehearsal_is_not_reconciled(app_engine, engine):
    _register(engine, SIM, "synthetic")
    sf = make_session_factory(app_engine)
    with sf() as s, s.begin():
        S.start(sf, "operator-test", SIM, "cpu_saturation", FIXED_NOW)
    assert S.reconcile_interrupted(sf, S.make_submit(sf, EnvelopeSchemaValidator(SCHEMA_PATH), Clock(FIXED_NOW)), Clock(FIXED_NOW)) == 0


# ---------------------------------------------------------------------------------------------- 3. browser API and grants
@pytest.fixture
def api(app_engine, monkeypatch):
    launched: list[uuid.UUID] = []
    monkeypatch.setattr(S, "launch", lambda sf, rid, submit, clock: launched.append(rid))   # no background thread in API tests
    store = RegistryCredentialStore(make_session_factory(app_engine))
    app = create_app(app_engine, store, clock=lambda: FIXED_NOW, verifier=verifier())
    client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app, raise_app_exceptions=False), base_url="http://test")
    client.launched = launched  # type: ignore[attr-defined]
    return client


async def _start(api, host_id=SIM, scenario="cpu_saturation", confirm=None):
    return await api.post("/api/v1/rehearsals", json={"host_id": host_id, "scenario": scenario,
                                                       "confirm_host_id": confirm if confirm is not None else host_id}, headers=bearer())


@pytest.mark.anyio
async def test_rehearsals_require_an_authorised_browser_session(api):
    assert (await api.get("/api/v1/rehearsals/targets")).status_code == 401
    assert (await api.post("/api/v1/rehearsals", json={})).status_code == 401


@pytest.mark.anyio
async def test_targets_list_marks_only_synthetic_hosts_as_eligible(api, engine):
    _register(engine, SIM, "synthetic")
    _register(engine, LIVE, "live")
    rows = {t["host_id"]: t for t in (await api.get("/api/v1/rehearsals/targets", headers=bearer())).json()}
    assert rows[SIM]["eligible"] is True and rows[SIM]["reason"] is None
    assert rows[LIVE]["eligible"] is False and "live" in rows[LIVE]["reason"]


@pytest.mark.anyio
async def test_a_live_host_can_never_be_targeted(api, engine):
    _register(engine, LIVE, "live")
    r = await _start(api, host_id=LIVE)
    assert r.status_code == 409 and r.json()["error"]["code"] == "host_not_eligible"
    with engine.connect() as c:
        assert c.execute(text("SELECT count(*) FROM failure_rehearsals")).scalar_one() == 0
    assert api.launched == []


@pytest.mark.anyio
async def test_unregistered_and_replay_and_deactivated_hosts_are_refused(api, engine):
    assert (await _start(api, host_id="sim-nobody-01")).json()["error"]["code"] == "host_not_registered"
    _register(engine, "replay-x-01", "replay")
    assert (await _start(api, host_id="replay-x-01")).json()["error"]["code"] == "host_not_eligible"
    _register(engine, "sim-retired-01", "synthetic")
    with engine.begin() as c:
        c.execute(text("UPDATE hosts SET deactivated_at = :n WHERE host_id = 'sim-retired-01'"), {"n": FIXED_NOW})
    assert (await _start(api, host_id="sim-retired-01")).json()["error"]["code"] == "host_deactivated"
    assert api.launched == []


@pytest.mark.anyio
async def test_start_requires_the_typed_host_confirmation_and_a_catalogue_scenario(api, engine):
    _register(engine, SIM, "synthetic")
    r = await _start(api, confirm="sim-other-01")
    assert r.status_code == 422
    r = await _start(api, scenario="disk_fill")
    assert r.status_code == 422
    assert api.launched == []


@pytest.mark.anyio
async def test_start_creates_one_running_rehearsal_and_a_second_is_refused(api, engine):
    _register(engine, SIM, "synthetic")
    r = await _start(api)
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["execution"] == "RUNNING" and body["detection"] is None and body["planned_envelopes"] == 11
    assert api.launched == [uuid.UUID(body["id"])]
    again = await _start(api)
    assert again.status_code == 409 and again.json()["error"]["code"] == "rehearsal_already_running"


@pytest.mark.anyio
async def test_stop_is_recorded_once_and_the_run_is_left_to_its_executor(api, engine):
    _register(engine, SIM, "synthetic")
    rid = (await _start(api)).json()["id"]
    first = (await api.post(f"/api/v1/rehearsals/{rid}/stop", headers=bearer())).json()
    assert first["stop_requested_at"] is not None and first["execution"] == "RUNNING"
    second = (await api.post(f"/api/v1/rehearsals/{rid}/stop", headers=bearer())).json()
    assert second["stop_requested_at"] == first["stop_requested_at"]


@pytest.mark.anyio
async def test_detail_returns_steps_and_unknown_ids_are_not_found(api, engine):
    _register(engine, SIM, "synthetic")
    rid = (await _start(api)).json()["id"]
    detail = (await api.get(f"/api/v1/rehearsals/{rid}", headers=bearer())).json()
    assert detail["steps"] == []
    assert (await api.get(f"/api/v1/rehearsals/{uuid.uuid4()}", headers=bearer())).status_code == 404


@pytest.mark.anyio
async def test_the_catalogue_states_what_is_affected_and_for_how_long(api):
    body = (await api.get("/api/v1/rehearsals/catalogue", headers=bearer())).json()
    by_id = {s["id"]: s for s in body["scenarios"]}
    assert set(by_id) == {"cpu_saturation", "memory_saturation"}
    cpu = by_id["cpu_saturation"]
    assert cpu["expected_duration_seconds"] == 150 and cpu["metric"] == "system.cpu.utilization" and cpu["metric_label"] == "CPU"
    assert "No process, load or CPU is changed on any machine" in cpu["affects"]
    mem = by_id["memory_saturation"]
    assert mem["metric"] == "system.memory.utilization" and mem["metric_label"] == "Memory"
    assert mem["expected_duration_seconds"] == 150 and "No memory is allocated on any machine" in mem["affects"]


@pytest.mark.anyio
async def test_a_cpu_warning_limit_above_the_fault_floor_blocks_the_start(api, engine, app_engine):
    _register(engine, SIM, "synthetic")
    sf = make_session_factory(app_engine)
    doc = {"freshness": {"stale_after_seconds": 90, "offline_after_seconds": 300},
           "rules": {"system.cpu.utilization": {"warning": 95, "critical": 99},
                     "system.memory.utilization": {"warning": 85, "critical": 95},
                     "system.disk.utilization": {"warning": 90, "critical": 95}}}
    with sf() as s, s.begin():
        cfg_store.save(s, S.RS.default_workspace_id(s), "operator-test", doc, 0, FIXED_NOW)
    r = await _start(api)
    assert r.status_code == 409 and r.json()["error"]["code"] == "scenario_cannot_cross_threshold"
    assert api.launched == []


def test_the_database_allows_one_running_rehearsal_per_workspace(engine):
    _register(engine, SIM, "synthetic")
    with engine.begin() as c:
        wid = c.execute(text("SELECT id FROM workspaces WHERE slug = 'default'")).scalar_one()
        hid = c.execute(text("SELECT id FROM hosts WHERE host_id = :h"), {"h": SIM}).scalar_one()

        def insert():
            c.execute(text("INSERT INTO failure_rehearsals (id, workspace_id, host_pk, host_id, scenario, execution, created_by, "
                           "planned_envelopes, config) VALUES (gen_random_uuid(), :w, :h, :hid, 'cpu_saturation', 'RUNNING', 't', 11, '{}')"),
                      {"w": wid, "h": hid, "hid": SIM})
        insert()
        with pytest.raises(IntegrityError):
            insert()


def test_the_application_role_cannot_delete_rehearsal_records(app_engine, engine):
    _register(engine, SIM, "synthetic")
    with engine.begin() as c:
        wid = c.execute(text("SELECT id FROM workspaces WHERE slug = 'default'")).scalar_one()
        hid = c.execute(text("SELECT id FROM hosts WHERE host_id = :h"), {"h": SIM}).scalar_one()
        c.execute(text("INSERT INTO failure_rehearsals (id, workspace_id, host_pk, host_id, scenario, execution, created_by, "
                       "planned_envelopes, config) VALUES (gen_random_uuid(), :w, :h, :hid, 'cpu_saturation', 'COMPLETED', 't', 11, '{}')"),
                  {"w": wid, "h": hid, "hid": SIM})
    with pytest.raises(DBAPIError):
        with app_engine.begin() as c:
            c.execute(text("DELETE FROM failure_rehearsals"))


# ------------------------------------------------------------------------------------ 4. memory saturation scenario
from app.rehearsal import samples as SA  # noqa: E402
from simulator.scenarios import normal_baseline  # noqa: E402

MEM = "system.memory.utilization"


def test_memory_ramp_stays_below_the_warning_limit_first_then_crosses_it_and_ends_above_the_floor():
    import random
    rng = random.Random(7)
    values = [SA.memory_ramp(j, 6, rng).memory for j in range(6)]
    assert values[0] < 85.0            # the first fault envelope alone does not breach the default warning limit
    assert values[1] >= 85.0           # the second one does: the crossing is a real, observable onset
    assert values[-1] >= C.SCENARIOS["memory_saturation"].reach_floor
    assert all(v <= 100.0 for v in values)


def test_memory_ramp_leaves_the_other_metrics_at_the_normal_baseline():
    import random
    sample = SA.memory_ramp(3, 6, random.Random(1))
    base = normal_baseline(3, 6, random.Random(1))
    assert sample.cpu == base.cpu and sample.disk_root == base.disk_root and sample.rx_rate == base.rx_rate


def _mem_steps(app_engine, rid):
    with app_engine.connect() as c:
        return [r[0] for r in c.execute(text(
            "SELECT observed_value FROM failure_rehearsal_steps WHERE rehearsal_id = :r AND phase = 'fault' ORDER BY seq"), {"r": rid})]


def test_memory_happy_path_is_detected_by_the_real_alert_pipeline_and_recovers(app_engine, engine):
    _register(engine, SIM, "synthetic")
    row = _execute(app_engine, Clock(FIXED_NOW), scenario="memory_saturation")
    assert row.scenario == "memory_saturation" and row.config["metric"] == MEM
    assert row.execution == E.EXEC_COMPLETED, row.execution_reason
    assert row.detection == E.DET_DETECTED, row.detection_reason
    assert "Memory alert opened" in row.detection_reason
    assert 0.0 <= row.detection_delay_seconds <= C.DETECTION_BUDGET_SECONDS
    assert 85.0 <= row.fault_observed_value < 95.0     # the first stored breach is the crossing, not the peak
    assert row.recovery_observed is True and row.recovery_seconds is not None
    assert row.evidence_useful is True
    assert len(_mem_steps(app_engine, row.id)) == 6
    with app_engine.connect() as c:
        keys = [r[0] for r in c.execute(text("SELECT condition_key FROM alerts WHERE id = :a"), {"a": row.alert_id})]
    assert keys == [MEM + "|{}"]


def test_memory_fault_values_are_read_back_from_storage_and_rise(app_engine, engine):
    _register(engine, SIM, "synthetic")
    row = _execute(app_engine, Clock(FIXED_NOW), scenario="memory_saturation")
    values = _mem_steps(app_engine, row.id)
    assert len(values) == 6 and values == sorted(values) and values[-1] >= 95.0


def test_memory_failed_injection_is_failed_and_never_detected(app_engine, engine):
    _register(engine, SIM, "synthetic")

    def store_down_for_fault(inner):
        def submit(doc):
            if any(m["name"] == MEM and m["value"] >= 80 for m in doc["metrics"]):
                raise RuntimeError("telemetry store unavailable")
            return inner(doc)
        return submit

    row = _execute(app_engine, Clock(FIXED_NOW), scenario="memory_saturation", wrap=store_down_for_fault)
    assert row.execution == E.EXEC_FAILED and row.detection == E.DET_INCONCLUSIVE and row.alert_id is None


def test_memory_fault_that_never_reaches_the_warning_limit_is_a_failed_injection(app_engine, engine):
    _register(engine, SIM, "synthetic")

    def memory_stays_low(inner):
        def submit(doc):
            for m in doc["metrics"]:
                if m["name"] == MEM and m["value"] >= 80:
                    m["value"] = 60.0
            return inner(doc)
        return submit

    row = _execute(app_engine, Clock(FIXED_NOW), scenario="memory_saturation", wrap=memory_stays_low)
    assert row.execution == E.EXEC_FAILED and "never reached the Memory warning limit" in row.execution_reason
    assert row.detection == E.DET_INCONCLUSIVE and row.alert_id is None


def test_missing_memory_detection_is_reported_as_undetected(app_engine, engine, monkeypatch):
    _register(engine, SIM, "synthetic")
    monkeypatch.setattr(S, "evaluate_after_ingest", lambda *a, **k: None)
    row = _execute(app_engine, Clock(FIXED_NOW), scenario="memory_saturation")
    assert row.execution == E.EXEC_COMPLETED and row.fault_confirmed_at is not None
    assert row.detection == E.DET_UNDETECTED and row.alert_id is None


def test_manual_stop_during_memory_fault_ends_it_with_cleanup_and_recovery(app_engine, engine):
    _register(engine, SIM, "synthetic")
    row = _execute(app_engine, Clock(FIXED_NOW), scenario="memory_saturation", stop_at=4)
    assert row.execution == E.EXEC_STOPPED and row.fault_sent == 2
    assert row.detection == E.DET_DETECTED and row.cleanup_status == "recovered"
    assert row.recovery_observed is True
    assert any(p == "cleanup" and o == "accepted" for _, p, o, _ in _steps(app_engine, row.id))


def test_a_live_cpu_alert_is_never_credited_to_a_memory_rehearsal(app_engine, engine):
    """The alert lookup is keyed on the scenario's own metric. A CPU alert opened by other telemetry must not decide a memory run."""
    _register(engine, SIM, "synthetic")
    sf = make_session_factory(app_engine)
    before = Clock(FIXED_NOW - timedelta(seconds=30))
    submit = S.make_submit(sf, EnvelopeSchemaValidator(SCHEMA_PATH), before)
    when = before()
    submit(make_envelope(host_id=SIM, agent_id="sim-rehearsal-agent", hostname="sim.local", timestamp=_ts(when),
                         metrics=[_cpu_metric(97.0, when)]))
    row = _execute(app_engine, Clock(FIXED_NOW), scenario="memory_saturation")
    assert row.detection == E.DET_DETECTED and row.config["metric"] == MEM


@pytest.mark.anyio
async def test_memory_start_is_refused_for_live_replay_and_deactivated_hosts(api, engine):
    _register(engine, LIVE, "live")
    r = await _start(api, host_id=LIVE, scenario="memory_saturation")
    assert r.status_code == 409 and r.json()["error"]["code"] == "host_not_eligible"
    _register(engine, "replay-y-01", "replay")
    assert (await _start(api, host_id="replay-y-01", scenario="memory_saturation")).json()["error"]["code"] == "host_not_eligible"
    _register(engine, "sim-gone-01", "synthetic")
    with engine.begin() as c:
        c.execute(text("UPDATE hosts SET deactivated_at = :n WHERE host_id = 'sim-gone-01'"), {"n": FIXED_NOW})
    assert (await _start(api, host_id="sim-gone-01", scenario="memory_saturation")).json()["error"]["code"] == "host_deactivated"
    assert api.launched == []


@pytest.mark.anyio
async def test_memory_start_creates_a_memory_rehearsal_and_reports_its_metric(api, engine):
    _register(engine, SIM, "synthetic")
    r = await _start(api, scenario="memory_saturation")
    assert r.status_code == 201, r.text
    assert r.json()["metric"] == MEM and r.json()["scenario"] == "memory_saturation"


@pytest.mark.anyio
async def test_memory_warning_above_the_memory_floor_blocks_only_the_memory_scenario(api, engine, app_engine):
    _register(engine, SIM, "synthetic")
    sf = make_session_factory(app_engine)
    doc = {"freshness": {"stale_after_seconds": 90, "offline_after_seconds": 300},
           "rules": {"system.cpu.utilization": {"warning": 85, "critical": 95},
                     "system.memory.utilization": {"warning": 96, "critical": 99},
                     "system.disk.utilization": {"warning": 90, "critical": 95}}}
    with sf() as s, s.begin():
        cfg_store.save(s, S.RS.default_workspace_id(s), "operator-test", doc, 0, FIXED_NOW)
    r = await _start(api, scenario="memory_saturation")
    assert r.status_code == 409 and r.json()["error"]["code"] == "scenario_cannot_cross_threshold"
    assert "Memory warning limit" in r.json()["error"]["message"]
    assert api.launched == []
    ok = await _start(api, scenario="cpu_saturation")
    assert ok.status_code == 201


@pytest.mark.anyio
async def test_a_rehearsal_is_listed_under_the_alert_it_raised_and_under_no_other(api, engine, app_engine):
    _register(engine, SIM, "synthetic")
    row = _execute(app_engine, Clock(FIXED_NOW))
    assert row.alert_id is not None
    hit = await api.get(f"/api/v1/rehearsals?alert_id={row.alert_id}", headers=bearer())
    assert hit.status_code == 200 and [r["id"] for r in hit.json()["rehearsals"]] == [str(row.id)]
    miss = await api.get(f"/api/v1/rehearsals?alert_id={uuid.uuid4()}", headers=bearer())
    assert miss.status_code == 200 and miss.json()["rehearsals"] == []
