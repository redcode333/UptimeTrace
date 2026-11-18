"""Spec 3.1-3.3 (profile, workload, baseline) and 4.1-4.4 (fault mechanisms, impact, recovery, outcome rule)."""
from __future__ import annotations

import numpy as np
import pytest

from simulator_v2 import baseline as B
from simulator_v2 import constants as C
from simulator_v2.faults.common import SPM, ramp_weights
from simulator_v2.faults.families import REDRAW_START, SIM, Ctx, GenerationAborted, draw_until_outcome
from simulator_v2.rng import RunStreams

N = C.N_STEPS


def make(idx=3, seed=0, archetype="web_api"):
    s = RunStreams(seed, idx)
    p = B.draw_profile(s, idx, "train", archetype, False)
    lam, sm = B.build_workload(p, s)
    return s, p, B.build_baseline(p, lam, sm, s)


def test_workload_mean_is_exactly_one_and_baseline_is_deterministic():
    s, p, bl = make()
    assert abs(bl.lam.mean() - 1.0) < 1e-12 and bl.lam.min() >= 0
    _, _, bl2 = make()
    assert np.array_equal(bl.cpu, bl2.cpu) and np.array_equal(bl.mem, bl2.mem) and np.array_equal(bl.tx, bl2.tx)
    _, _, bl3 = make(seed=1)
    assert not np.array_equal(bl.cpu, bl3.cpu)


def test_profile_draws_stay_inside_the_spec_ranges():
    for idx in range(60):
        s = RunStreams(7, idx)
        p = B.draw_profile(s, idx, "train", ["web_api", "worker_batch", "database", "queue_consumer"][idx % 4], False)
        assert 8 <= p.cpu_base <= 40 and 25 <= p.mem_base <= 70 and 30 <= p.disk_root_base <= 75 and 15 <= p.disk_data_base <= 70
        assert 0.3 <= p.s_cpu <= 0.9 and 0.05 <= p.s_mem <= 0.3 and p.nic_bps in (1.25e8, 1.25e9)
        assert 0.005 * p.nic_bps <= p.rx_base <= 0.10 * p.nic_bps and 0.3 <= p.tx_base / p.rx_base <= 3.0
        assert 10 ** 9 <= p.c0_rx <= 5 * 10 ** 12 and abs(p.skew_s) <= C.S_MAX and p.run_start_epoch % 30 == 0
        assert 0.6 <= min(p.noise_phi60.values()) and max(p.noise_phi60.values()) <= 0.95
        assert 0.62 <= min(p.sigma_marg[k] for k in C.SERIES[:4]) and max(p.sigma_marg[k] for k in C.SERIES[:4]) <= 9.62
        assert 0.05 <= p.sigma_marg["rx"] <= 0.30 and p.host_id == f"sim-r{idx:04d}"
        assert p.hostname.endswith(".sim.local")


def test_weekend_factor_and_deploy_shift_bounds():
    s = RunStreams(0, 0)
    p = B.draw_profile(s, 0, "train", "web_api", False)
    p.a1 = p.a2 = 0.0; p.sigma_ou = 1e-9; p.burst_rate_per_day = 0.0; p.tz_offset_h = 0.0
    p.run_start_epoch = B.EPOCH_2024                         # 2024-01-01 was a Monday
    p.weekend_factor = 0.5
    lam, _ = B.build_workload(p, s)
    mon, sat = lam[:2880].mean(), lam[5 * 2880:6 * 2880].mean()
    assert sat / mon == pytest.approx(0.5, rel=1e-6)
    bl = B.build_baseline(p, lam, lam, s)
    assert bl.f_shift.min() >= 0.5 and bl.f_shift.max() <= 1.6


@pytest.mark.parametrize("family", ["mem_leak", "disk_fill", "cpu_runaway", "net_flood", "pool_exhaustion"])
@pytest.mark.parametrize("planned", ["incident", "twin"])
def test_simulated_outcome_equals_planned_and_contribution_is_zero_at_conflict_end(family, planned):
    s, p, bl = make()
    ctx = Ctx(p, bl)
    for rep in range(8):
        start = 1500 + 3500 * rep
        f, attempts = draw_until_outcome(family, ctx, start, planned, lambda k, r=rep: np.random.default_rng([r, k, 11]))
        assert f.outcome == planned and 1 <= attempts <= C.MAX_ATTEMPTS
        for sig, (mul, add) in f.eff.items():
            assert len(mul) == len(add) == f.window_len
            assert mul[-1] == 1.0 and add[-1] == 0.0, (family, sig)          # exactly zero at conflict_end
        for arr in f.delta.values():
            assert arr[-1] == 0.0
        assert f.conflict_end_i == f.start_i + f.window_len - 1 <= N
        if planned == "incident":
            assert (f.impact_end_i - f.impact_start_i) * C.LATENT_DT >= C.INCIDENT_MIN_DURATION
            assert f.start_i <= f.impact_start_i < f.impact_end_i < f.conflict_end_i
            assert (f.conflict_end_i - f.impact_end_i) in range(10, 41)       # T_ret ~ U(5,20) min
        else:
            assert f.impact_start_i is None and f.resolved_i is not None and f.peak_i >= f.start_i


def test_tret_is_capped_at_14_minutes_for_relapse_designated_primaries():
    s, p, bl = make()
    ctx = Ctx(p, bl)
    for rep in range(12):
        f, _ = draw_until_outcome("silent_crash", ctx, 3000 + 100 * rep, "incident",
                                  lambda k, r=rep: np.random.default_rng([r, k, 5]), tret_max_min=C.RELAPSE_TRET_MAX_MIN)
        assert 5 * SPM <= f.tret_steps <= 14 * SPM


def test_recovery_times_respect_the_clips():
    s, p, bl = make()
    ctx = Ctx(p, bl)
    clips = {"process_crash": (2, 30), "dependency_outage": (3, 120), "kernel_panic": (4, 20)}
    seen = set()
    for rep in range(150):
        f = SIM["silent_crash"](ctx, 2000, "incident", np.random.default_rng([rep, 9]))
        mins = (f.impact_end_i - f.impact_start_i) / SPM
        lo, hi = clips[f.subtype]
        assert lo - 1e-9 <= mins <= hi + 0.5
        seen.add(f.subtype)
    assert seen == set(clips)


def test_oom_panic_share_is_about_15_percent_and_panic_resets_counters_at_impact_end():
    s, p, bl = make()
    ctx = Ctx(p, bl)
    n = pan = 0
    for rep in range(500):
        f = SIM["mem_leak"](ctx, 2000, "incident", np.random.default_rng([rep, 3]))
        if f is None or f is REDRAW_START:
            continue
        n += 1
        if f.panic_gap:
            pan += 1
            assert f.panic_gap == (f.impact_start_i, f.impact_end_i) and f.reset_i == f.impact_end_i
            assert f.footprint_class == "F10" and f.variant == "oom_panic"
        else:
            assert f.reset_i is None and f.footprint_class == "F9"
    assert n > 300 and 0.09 <= pan / n <= 0.21


def test_kernel_panic_and_silent_crash_have_no_pre_onset_contribution():
    s, p, bl = make()
    ctx = Ctx(p, bl)
    for rep in range(60):
        f = SIM["silent_crash"](ctx, 4000, "incident", np.random.default_rng([rep, 2]))
        assert f.impact_start_i == f.start_i                           # no lead-in at all, so zero contribution before onset
        if f.subtype == "kernel_panic":
            assert f.eff == {} and f.panic_gap == (f.start_i, f.impact_end_i) and f.reset_i == f.impact_end_i
            assert f.footprint_class == "F10"
        else:
            assert f.reset_i is None and f.panic_gap is None


def test_mem_leak_onset_is_first_time_noise_free_memory_reaches_theta_plus_delay():
    s, p, bl = make()
    ctx = Ctx(p, bl)
    f = draw_until_outcome("mem_leak", ctx, 3000, "incident", lambda k: np.random.default_rng([4, k]))[0]
    theta, kappa = f.params["theta"], f.params["kappa"]
    L = f.delta["mem"]
    k_imp = f.impact_start_i - f.start_i
    first = int(np.nonzero(bl.mem[f.start_i:f.start_i + k_imp + 1] + L[:k_imp + 1] >= theta)[0][0])
    assert first + f.params["oom_delay_steps"] == k_imp
    assert 0 <= f.params["oom_delay_steps"] <= 5 * SPM
    assert L[k_imp] > 0 and np.all(L[k_imp + 1:] == 0)                 # L resets at the next grid point


def test_cpu_incident_condition_requires_120s_and_twin_never_meets_it():
    s, p, bl = make()
    ctx = Ctx(p, bl)
    checked = 0
    for rep in range(20):
        f = SIM["cpu_runaway"](ctx, 3000, "incident", np.random.default_rng([rep, 8]))
        if f is None:
            continue
        checked += 1
        rho = (bl.cpu[f.start_i:f.conflict_end_i + 1] + f.delta["cpu"]) / 100
        k = f.impact_start_i - f.start_i
        crit = f.params["rho_crit"]
        assert np.all(rho[k:k + 5] >= crit)                                 # >= 5 consecutive points = 120 s
        assert not np.any(np.convolve((rho[:k + 4] >= crit).astype(int), np.ones(5, int), "valid") == 5)
    assert checked >= 10
    twin_ok = 0
    for rep in range(40):
        f = SIM["cpu_runaway"](ctx, 3000, "twin", np.random.default_rng([rep, 6]))
        if f is not None and f.outcome == "twin":
            rho = (bl.cpu[f.start_i:f.conflict_end_i + 1] + f.delta["cpu"]) / 100
            ok = (rho >= f.params["rho_crit"]).astype(int)
            assert not np.any(np.convolve(ok, np.ones(5, int), "valid") == 5)
            twin_ok += 1
    assert twin_ok > 5


def test_return_ramp_is_linear_over_t_ret():
    w = ramp_weights(30, 5, 12, 10)
    assert w[:5].sum() == 0 and np.all(w[5:13] == 1) and w[22] == 0.0 and w[21] == pytest.approx(0.1)
    assert np.allclose(np.diff(w[12:23]), -0.1)
    s, p, bl = make()
    f = SIM["cpu_runaway"](Ctx(p, bl), 3000, "incident", np.random.default_rng(1))
    c = f.delta["cpu"]
    k_end = f.impact_end_i - f.start_i
    ramp = c[k_end:]
    assert ramp[0] > 0 and ramp[-1] == 0.0 and np.allclose(np.diff(ramp), -ramp[0] / (len(ramp) - 1))


def test_attempt_limit_aborts_after_exactly_1000_attempts():
    s, p, bl = make()
    calls = []

    def rng_of(k):
        calls.append(k)
        return np.random.default_rng(k)
    with pytest.raises(GenerationAborted, match="1000 attempts"):
        draw_until_outcome("mem_leak", Ctx(p, bl), 3000, "incident", rng_of, accept=lambda f: False)
    assert calls == list(range(1, 1001))


def test_ood_slow_ramps_scale_lead_times():
    s, p, bl = make()
    normal = [SIM["pool_exhaustion"](Ctx(p, bl, 1.0), 2000, "incident", np.random.default_rng([r, 1])) for r in range(40)]
    slow = [SIM["pool_exhaustion"](Ctx(p, bl, C.OOD_SLOWDOWN), 2000, "incident", np.random.default_rng([r, 1])) for r in range(40)]
    ln = [f.params["D_min"] for f in normal if f]
    ls = [f.params["D_min"] for f in slow if f]
    assert np.median(ls) / np.median(ln) == pytest.approx(C.OOD_SLOWDOWN, rel=0.01)
