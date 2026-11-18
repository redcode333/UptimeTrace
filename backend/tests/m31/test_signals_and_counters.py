"""AC-13 counter properties and AC-32 reset behaviour; noise model of spec 3.4."""
from __future__ import annotations

from fractions import Fraction

import numpy as np
import pytest

from simulator_v2 import constants as C
from simulator_v2.baseline import grid_ceil
from simulator_v2.signals import ar1, build_latent
from simulator_v2.planner import RunPlan

N = C.N_STEPS
DT = C.LATENT_DT
U30 = np.arange(0, C.RUN_LEN, DT, dtype=float)       # jitter-off, cadence-30 sampling instants


def test_ar1_has_the_requested_stationary_std_and_lag1_autocorrelation():
    rng = np.random.default_rng(1)
    z = ar1(rng, 400_000, 0.8, 2.5)
    assert z.std() == pytest.approx(2.5, rel=0.02)
    assert np.corrcoef(z[:-1], z[1:])[0, 1] == pytest.approx(0.8, abs=0.01)


def test_percent_noise_matches_profile_parameters_and_emission_is_clipped_and_rounded(truth_cache):
    t = truth_cache(95, True)
    p, lat = t.profile, t.latent
    for s in ("cpu", "mem", "disk_root", "disk_data"):
        nf = lat.nf[s]
        z = (lat.pct[s] - nf) / (1.0 + 0.5 * nf / 100.0)           # remove the heteroscedastic scale
        phi = p.noise_phi60[s] ** 0.5
        assert np.median(np.abs(z)) / 0.6745 == pytest.approx(p.sigma_marg[s], rel=0.2)       # robust to the spikes
        assert np.corrcoef(z[:-1], z[1:])[0, 1] == pytest.approx(phi, abs=0.06)
        v = lat.sample_pct(s, U30)
        assert v.min() >= 0 and v.max() <= 100 and np.allclose(v, np.round(v, 2))


def test_omission_mask_rate_and_spike_probability(truth_cache):
    t = truth_cache(95)
    assert t.omit.shape == (N + 1, 6) and t.omit.mean() == pytest.approx(C.OMIT_P, rel=0.15)


def reset_run(truth_cache):
    t = truth_cache(95, True)               # force_one_each guarantees a planned reboot
    assert t.latent.reset_idx.size >= 1
    return t


def test_counters_decrease_only_at_logged_resets_and_exactly_there(truth_cache):
    t = reset_run(truth_cache)
    lat = t.latent
    for s in ("rx", "tx"):
        v = lat.sample_counter(s, U30)
        dec = np.nonzero(np.diff(v) < 0)[0]
        # the decrease sits between the last pre-reset tick and the first tick with u >= reset time (right-continuous)
        assert [int(d) + 1 for d in dec] == [int(g) for g in lat.reset_idx]
        assert np.all(np.diff(v)[np.setdiff1d(np.arange(v.size - 1), dec)] >= 0)      # non-decreasing everywhere else
        assert all(v[g] < 10 ** 6 + 1 for g in lat.reset_idx)                 # fresh base in U(0, 1e6) then integrating
    resets = {g for g, _ in lat.resets["rx"]}
    assert resets == {int(g) for g in lat.reset_idx}


def test_right_continuity_at_the_reset_instant(truth_cache):
    t = reset_run(truth_cache)
    lat = t.latent
    g = int(lat.reset_idx[0])
    before = lat.sample_counter("rx", np.array([g * DT - 1e-6]))[0]
    at = lat.sample_counter("rx", np.array([g * DT]))[0]
    after = lat.sample_counter("rx", np.array([g * DT + 1.0]))[0]
    assert at < before and after >= at and at <= 10 ** 6 + lat.inc["rx"][g]


def test_emitted_increment_matches_the_integral_within_one(truth_cache):
    t = truth_cache(95)
    lat = t.latent
    rng = np.random.default_rng(3)
    resets = set(int(g) for g in lat.reset_idx)
    checked = 0
    for _ in range(400):
        i = int(rng.integers(0, N - 200))
        j = i + int(rng.integers(1, 150))
        if any(i < g <= j for g in resets):
            continue
        u1, u2 = float(rng.integers(0, 30000)) / 1000 + i * DT, float(rng.integers(0, 30000)) / 1000 + j * DT
        e1 = lat.sample_counter("tx", np.array([u1]))[0]
        e2 = lat.sample_counter("tx", np.array([u2]))[0]

        def exact(u, k):
            ki = int(u // DT)
            return Fraction(int(lat.cnt_grid["tx"][ki])) + Fraction(int(lat.inc["tx"][ki])) * Fraction(round(u * 1000), 1000 * DT) - Fraction(int(lat.inc["tx"][ki])) * ki
        integral = exact(u2, 0) - exact(u1, 0)
        assert abs(int(e2 - e1) - integral) <= 1
        checked += 1
    assert checked > 300


def test_totals_stay_below_2_pow_53_even_at_maximum_rate_and_initial_value(truth_cache):
    t = truth_cache(95)
    p = t.profile
    from copy import copy
    pp = copy(p)
    pp.c0_rx = pp.c0_tx = 5 * 10 ** 12
    pp.nic_bps = 1.25e9
    base = copy(t.base)
    base.rx = np.full(N + 1, 1e11); base.tx = np.full(N + 1, 1e11)         # far above the NIC: clipped to NIC_Bps
    ep = {"reboots": [], "blips": [], "blip_params": [], "agent_down": [], "partitions": [], "panic": []}
    lat = build_latent(pp, base, RunPlan(), ep, t.streams)
    top = int(lat.cnt_grid["rx"].max())
    assert top < 2 ** 53 and top > 5 * 10 ** 12 + 1.0e15 * 0.5               # large, but exactly representable
    assert lat.inc["rx"].max() <= int(1.25e9 * DT)
    assert int(lat.cnt_grid["rx"][-1]) == 5 * 10 ** 12 + int(lat.inc["rx"].sum())      # exact integer arithmetic


def test_counters_keep_integrating_through_dropouts_and_blips_dont_reset(truth_cache):
    t = truth_cache(95, True)
    lat = t.latent
    for (a, b) in t.episodes["agent_down"] + t.episodes["partitions"]:
        assert not (set(range(a + 1, b + 1)) & {int(g) for g in lat.reset_idx})
    for (a, b) in t.episodes["blips"]:
        assert not (set(range(a + 1, b + 1)) & {int(g) for g in lat.reset_idx})


def test_reset_sources_are_exactly_planned_reboots_and_panic_faults(truth_cache):
    for idx in (0, 44, 95, 125, 170):
        t = truth_cache(idx, True)
        expected = {e for (_, e) in t.episodes["reboots"]} | {pf.sim.reset_i for pf in t.plan.faults if pf.sim.reset_i is not None}
        assert {int(g) for g in t.latent.reset_idx} == expected
        for pf in t.plan.faults:
            if pf.sim.reset_i is not None:
                assert pf.sim.footprint_class == "F10" and pf.sim.family in ("mem_leak", "silent_crash")
