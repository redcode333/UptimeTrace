"""AC-14 (timestamps, membership, availability bounds, realizable timing fixture) and AC-16 (accounting I1-I11)."""
from __future__ import annotations

from datetime import datetime

import numpy as np
import pytest

from simulator_v2 import constants as C
from simulator_v2.bounds import buffered_bounds, c_max, normal_bounds
from simulator_v2.rng import RunStreams
from simulator_v2.run import render_view
from simulator_v2.ticks import (BUFFERED, LOST_AGENT, NORMAL, _classify, account, build_run_output, chunk_observations,
                                truncated_jitter)

DT = C.LATENT_DT


def parse(ts):
    return datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp()


def identities(d):
    ticks_normal = d["normal_emitting"] + d["normal_all_omitted"]
    ticks_buf = d["buffered_emitting"] + d["buffered_all_omitted"]
    produced = ticks_normal + ticks_buf
    env_total = d["envelopes_normal"] + d["envelopes_replay"]
    obs_total = d["observations_normal"] + d["observations_buffered"]
    return {
        "I1": d["ticks_nominal"] == produced + d["lost_agent_down"] + d["lost_planned_reboot_gap"] + d["lost_panic_gap"],
        "I2": produced == ticks_normal + ticks_buf,
        "I5": d["envelopes_normal"] == d["normal_emitting"] == ticks_normal - d["normal_all_omitted"],
        "I6": d["partitions_with_replay"] <= d["envelopes_replay"],
        "I7": env_total == d["envelopes_normal"] + d["envelopes_replay"],
        "I8": d["observations_normal"] == 6 * ticks_normal - d["observations_omitted_normal"]
              and d["observations_buffered"] == 6 * ticks_buf - d["observations_omitted_buffered"],
        "I9": obs_total == 6 * produced - (d["observations_omitted_normal"] + d["observations_omitted_buffered"]),
    }


# ---------------------------------------------------------------------------------------------- spec 10.1 worked example
def worked_example():
    code = np.array([NORMAL] * 5 + [BUFFERED] * 4 + [NORMAL] + [LOST_AGENT] * 2, dtype=np.int8)
    part = np.array([-1] * 5 + [0] * 4 + [-1] * 3)
    om = np.zeros((12, 6), dtype=bool)
    om[2, 0] = True          # tick 3: one observation omitted
    om[3, :] = True          # tick 4: normal, all six omitted
    om[7, :] = True          # tick 8: buffered, all six omitted
    return code, part, om


def test_worked_example_reproduces_exactly():
    d = account(*worked_example(), n_partitions=1)
    assert d["ticks_nominal"] == 12 and d["lost_agent_down"] == 2
    assert (d["normal_emitting"], d["normal_all_omitted"]) == (5, 1)
    assert (d["buffered_emitting"], d["buffered_all_omitted"]) == (3, 1)
    assert (d["partitions_total"], d["partitions_with_replay"]) == (1, 1)
    assert d["envelopes_normal"] == 5 and d["envelopes_replay"] == 1 and d["envelopes_normal"] + d["envelopes_replay"] == 6
    assert d["observations_candidate"] == 60
    assert (d["observations_omitted_normal"], d["observations_omitted_buffered"]) == (7, 6)
    assert (d["observations_normal"], d["observations_buffered"]) == (29, 18)
    assert d["observations_normal"] + d["observations_buffered"] == 47
    assert all(identities(d).values()), identities(d)
    # the v4.3 expression would have given 10 - 4 - 2 = 4 normal envelopes; the correct value is 5
    assert d["envelopes_normal"] != (d["normal_emitting"] + d["normal_all_omitted"] + d["buffered_emitting"] + d["buffered_all_omitted"]) - 4 - 2


def test_partition_with_only_a_fully_omitted_tick_sends_no_replay():
    code = np.array([NORMAL, BUFFERED, NORMAL], dtype=np.int8)
    om = np.zeros((3, 6), dtype=bool); om[1, :] = True
    d = account(code, np.array([-1, 0, -1]), om, 1)
    assert (d["partitions_total"], d["partitions_with_replay"], d["envelopes_replay"]) == (1, 0, 0)
    assert d["partitions_total"] - d["partitions_with_replay"] == 1
    d0 = account(np.array([NORMAL, NORMAL], dtype=np.int8), np.array([-1, -1]), np.zeros((2, 6), bool), 1)    # partition with no tick
    assert (d0["partitions_with_replay"], d0["envelopes_replay"]) == (0, 0)


def test_a_normal_tick_with_exactly_one_emitted_observation_still_sends_an_envelope():
    om = np.ones((1, 6), dtype=bool); om[0, 4] = False
    d = account(np.array([NORMAL], dtype=np.int8), np.array([-1]), om, 0)
    assert d["envelopes_normal"] == 1 and d["observations_normal"] == 1 and d["observations_omitted_normal"] == 5


def test_chunking_501_observations_gives_two_chunks():
    obs = list(range(501))
    ch = chunk_observations(obs)
    assert [len(c) for c in ch] == [500, 1] and sum(ch, []) == obs
    assert len(chunk_observations(list(range(500)))) == 1 and chunk_observations([]) == []
    om = np.zeros((84, 6), dtype=bool)                 # 84 buffered ticks -> 504 observations -> 2 chunks
    d = account(np.full(84, BUFFERED, dtype=np.int8), np.zeros(84, dtype=np.int64), om, 1)
    assert d["envelopes_replay"] == 2 and d["partitions_with_replay"] == 1


# ---------------------------------------------------------------------------------------------- real-run agreement
@pytest.fixture(scope="module")
def view60(truth_cache):
    t = truth_cache(95, True)
    return t, render_view(t, 60, True)


def test_ledger_matches_pure_accounting_and_artifacts(view60):
    t, out = view60
    ti = out.tick_info
    d = account(ti["code"], ti["partition"], ti["omit"], len(t.gaps_grid["net_partition_buffered"]))
    led = out.ledger.as_dict()
    assert all(led[k] == v for k, v in d.items()), {k: (led[k], v) for k, v in d.items() if led[k] != v}
    assert all(identities(d).values())
    assert out.n_envelopes == len(out.envelopes) == d["envelopes_normal"] + d["envelopes_replay"]
    assert sum(len(e["metrics"]) for e in out.envelopes) == d["observations_normal"] + d["observations_buffered"] == len(out.columns["value"])
    assert all(len(e["metrics"]) >= 1 for e in out.envelopes)
    assert len({e["envelope_id"] for e in out.envelopes}) == len(out.envelopes)


# ---------------------------------------------------------------------------------------------- timestamps (AC-14)
def test_nominal_ticks_start_at_run_start_and_jitter_skew_bounds_hold(view60):
    t, out = view60
    ti = out.tick_info
    n, u = ti["n"], ti["u"]
    assert n[0] == 0 and np.all(np.diff(n) == 60) and len(n) == 20160
    assert u[0] >= 0 and np.abs(u - n).max() <= C.J_MAX + 1e-9 and np.all(np.diff(u) >= 60 - 4 - 1e-9)
    T = ti["T_us"]
    assert np.all(np.diff(T) > 0)
    skew_us = np.rint(t.profile.skew_s * 1e6)
    assert np.abs((T - t.profile.run_start_epoch * 10 ** 6) - np.rint(u * 1e6) - skew_us).max() <= 1
    assert abs(t.profile.skew_s) <= C.S_MAX


def test_first_tick_is_clipped_at_run_start_when_jitter_is_negative(truth_cache):
    t = truth_cache(95)
    for seed in range(50):
        probe = truncated_jitter(RunStreams(seed, 0).fresh("jitter", 60), 20160)
        if probe[0] < 0:
            break
    else:
        pytest.fail("no negative first-tick jitter found")
    out = build_run_output(t.profile, t.latent, t.gaps_grid, 60, True, RunStreams(seed, 0), t.omit)
    u = out.tick_info["u"]
    assert u[0] == 0.0 and np.abs(u[1:] - out.tick_info["n"][1:]).max() <= C.J_MAX


def test_jitter_is_truncated_not_clipped():
    j = truncated_jitter(np.random.default_rng(0), 200_000)
    assert np.abs(j).max() <= 2.0 and (np.abs(j) == 2.0).sum() == 0 and j.std() == pytest.approx(0.5, rel=0.02)


def test_membership_is_by_true_sample_time_with_half_open_intervals():
    a, b = 3600.0, 5400.0
    u = np.array([a - 1e-9, a, b - 1e-9, b, b + 0.5])
    code, part = _classify(u, {"agent_down": [(a, b)]})
    assert list(code) == [NORMAL, LOST_AGENT, LOST_AGENT, NORMAL, NORMAL]
    code, part = _classify(u, {"net_partition_buffered": [(a, b)]})
    assert list(code) == [NORMAL, BUFFERED, BUFFERED, NORMAL, NORMAL] and list(part) == [-1, 0, 0, -1, -1]
    # a tick whose NOMINAL time is outside but whose true time is inside belongs to the gap (and vice versa)
    n_k, j = a - 1.5, 1.8
    assert n_k < a <= n_k + j and _classify(np.array([n_k + j]), {"agent_down": [(a, b)]})[0][0] == LOST_AGENT
    n_k2, j2 = b - 1.0, 1.5
    assert n_k2 < b and _classify(np.array([n_k2 + j2]), {"agent_down": [(a, b)]})[0][0] == NORMAL


def test_normal_and_replay_availability_bounds_on_a_real_run(view60):
    t, out = view60
    led = out.ledger
    ob, nb = normal_bounds()
    assert (ob, nb) == (0.4, 3.9)
    assert 0 < led.max_lag_obs_normal <= ob and 0 < led.max_lag_nominal_normal <= nb
    rb, rnb = buffered_bounds(c_max(60))
    assert led.max_lag_obs_buffered <= rb and led.max_lag_nominal_buffered <= 1806.5 <= rnb
    # envelope timestamp is the availability time: replay >= every contained observation and >= b + skew + 0.5 s
    by_id = {e["envelope_id"]: e for e in out.envelopes}
    for g, (a_i, b_i) in zip(out.view_gaps, t.gaps_grid["net_partition_buffered"]):
        for eid in g["replay_envelope_ids"]:
            e = by_id[eid]
            et = parse(e["timestamp"])
            assert all(parse(m["timestamp"]) <= et for m in e["metrics"])
            assert et >= t.profile.run_start_epoch + b_i * DT + t.profile.skew_s + 0.5 - 1e-6
            assert len(e["metrics"]) <= 500
    normal_ts = [parse(e["timestamp"]) for e in out.envelopes if e["envelope_id"] not in {i for g in out.view_gaps for i in g["replay_envelope_ids"]}]
    assert all(b > a for a, b in zip(normal_ts, normal_ts[1:]))               # normal envelope timestamps strictly increase


def test_c_max_is_one_for_every_permitted_cadence_and_two_for_a_too_fast_one():
    assert [c_max(c) for c in (30, 60, 120)] == [1, 1, 1]
    assert c_max(21) == 2 and buffered_bounds(2) == (1805.4, 1808.9)


# ---------------------------------------------------------------------------------------------- AC-14 (A) and (B)
def test_ac14_a_algebraic_check_of_the_conservative_bounds_uses_off_grid_inputs():
    """Hand-specified real-valued inputs, NOT a tick the simulator can generate (nominal time is off-grid)."""
    a, D = 3600.0, 1800.0
    b = a + D
    u, j, s, dr = a, +2.0, +1.5, 5.0
    n = u - j                                 # = a - 2 s: off the 30 s grid
    assert n % DT != 0
    T, E = u + s, b + s + dr
    assert E - T == 1805.0 == buffered_bounds(1)[0]
    assert E - n == 1808.5 == buffered_bounds(1)[1]
    # each component is at its maximum: lowering any one lowers the lag
    assert (E - 1) - n < 1808.5 and E - ((u + 1) + s) < 1805.0           # lowering delay or sampling later lowers the lag
    assert 1808.5 == C.D_MAX + C.J_MAX + C.S_MAX + C.REPLAY_DELAY[1]


def test_ac14_b_simulator_realizable_fixture_attains_1805_and_1806_5_but_not_1808_5(truth_cache):
    t = truth_cache(95)
    a_i, b_i = 120, 180                                           # a = 3600 s, b = 5400 s: D_max = 1800 s on the run_start grid
    gaps = {"net_partition_buffered": [(a_i, b_i)]}
    out = build_run_output(t.profile, t.latent, gaps, 60, False, RunStreams(0, 95), t.omit,
                           overrides={"replay_delay": 5.0, "skew_s": 1.5})
    led = out.ledger
    assert led.partitions_total == 1 and led.max_chunks == 1
    n = out.tick_info["n"]
    assert n[60] == a_i * DT and n[60] % 60 == 0                  # n_k = a lies on the run_start grid
    assert led.max_lag_obs_buffered == pytest.approx(1805.0, abs=1e-6)
    assert led.max_lag_nominal_buffered == pytest.approx(1806.5, abs=1e-6)
    assert led.max_lag_nominal_buffered < buffered_bounds(1)[1] == 1808.5        # the conservative bound is not attained by generated data
    # with jitter on (any realisation) the generated nominal-domain lag still cannot exceed 1806.5 s
    for seed in range(5):
        o2 = build_run_output(t.profile, t.latent, gaps, 60, True, RunStreams(seed, 95), t.omit,
                              overrides={"replay_delay": 5.0, "skew_s": 1.5})
        assert o2.ledger.max_lag_nominal_buffered <= 1806.5 + 1e-6 and o2.ledger.max_lag_obs_buffered <= 1805.0 + 1e-6
