"""AC-07, AC-09, FP9, FP10 and the planner rules of spec section 5, checked on real planned runs."""
from __future__ import annotations

from collections import Counter
from types import SimpleNamespace

import pytest

from simulator_v2 import constants as C
from simulator_v2 import planner as PL
from simulator_v2.footprints import MATRIX
from simulator_v2.planner import PlannedFault, placement_ok

N = C.N_STEPS
RUN_IDXS = [0, 13, 44, 71, 95, 110, 125, 140, 160, 170, 177, 179]      # all four splits


@pytest.fixture(scope="module")
def planned(truth_cache):
    return [truth_cache(i) for i in RUN_IDXS]


def groups_of(t):
    g = {}
    for pf in t.plan.faults:
        if pf.group is not None:
            g.setdefault(pf.group, []).append(pf)
    return [g[k] for k in sorted(g, key=lambda k: min(m.sim.start_i for m in g[k]))]


def test_plan_matches_quota_slots_and_twins_are_one_to_one(planned):
    for t in planned:
        prim = Counter(pf.sim.family for pf in t.plan.faults if pf.role == "primary")
        assert prim == Counter(f for f, _ in t.spec.primaries)
        tw = Counter(pf.sim.family for pf in t.plan.faults if pf.role == "twin")
        for fam in C.PRECURSOR_FAMILIES:
            assert tw[fam] == prim[fam] and tw[fam] >= 1 or prim[fam] == 0       # twins >= 1:1 per precursor family
        assert tw["silent_crash"] == 0
        assert sum(1 for pf in t.plan.faults if pf.role == "relapse") == sum(g for _, g in t.spec.primaries)


def test_warmup_end_of_run_and_attempt_limits(planned):
    for t in planned:
        for pf in t.plan.faults:
            assert pf.sim.start_i >= C.PLANNER_WARMUP // C.LATENT_DT
            assert pf.sim.conflict_end_i <= N
            assert 1 <= pf.attempts <= C.MAX_ATTEMPTS
            assert pf.sim.outcome == pf.planned_outcome


def test_sequential_groups_t_free_rule_and_oracle_masks_are_disjoint_and_inside_the_run(planned):
    for t in planned:
        gs = groups_of(t)
        for g in gs:
            R = max(m.sim.impact_end_i for m in g)
            mask_end = max(R + 30, max(m.sim.conflict_end_i for m in g))
            assert mask_end <= N                                            # group rejection rule == oracle_mask_end <= run_end
        for a, b in zip(gs, gs[1:]):
            R = max(m.sim.impact_end_i for m in a)
            conf = max(m.sim.conflict_end_i for m in a)
            start_b = min(m.sim.start_i for m in b)
            onset_b = min(m.sim.impact_start_i for m in b)
            assert start_b >= max(R + 31, conf)                             # T_free = max(R + 900 s + 30 s, max conflict_end)
            assert (onset_b - R) * C.LATENT_DT >= 930                       # AC-07
            assert onset_b >= max(R + 30, conf)                             # onset_{k+1} >= oracle_mask_end_k


def test_merged_incidents_reproduce_the_planned_groups(planned):
    for t in planned:
        incs = t.records["incidents"]
        assert len(incs) == len(t.spec.primaries)
        assert sorted(i["n_members"] for i in incs) == sorted(1 + g for _, g in t.spec.primaries)
        for i in incs:
            assert i["members"][0]["member_relation"] == "primary"
            assert i["family"] == next(m for m in i["members"])["family"]
        assert not any(m["fault_id"].endswith("tw") for i in incs for m in i["members"])
        twin_ids = {f["fault_id"] for f in t.records["faults"] if f["member_relation"] is None}
        assert twin_ids and not (twin_ids & {m["fault_id"] for i in incs for m in i["members"]})


def test_relapse_windows_and_forced_variants(planned):
    seen = 0
    for t in planned:
        for g in groups_of(t):
            if len(g) == 1:
                continue
            prim, rel = g[0].sim, g[1].sim
            seen += 1
            assert g[0].role == "primary" and g[1].role == "relapse" and rel.family == prim.family
            assert prim.family in C.RELAPSE_FAMILIES
            assert rel.start_i >= prim.conflict_end_i
            gap = (rel.impact_start_i - prim.impact_end_i) * C.LATENT_DT
            assert C.RELAPSE_JOIN_MIN <= gap <= C.RELAPSE_JOIN_MAX
            assert prim.tret_steps <= C.RELAPSE_TRET_MAX_MIN * 2
            if prim.family in ("cpu_runaway", "net_flood"):
                assert rel.variant in ("step",) or rel.params.get("shape") == "step" or rel.params.get("variant") == "step"
    assert seen >= 3


def test_fp3_fp4_observed_signals_are_inside_declared_footprints_and_never_shared(planned):
    import numpy as np
    for t in planned:
        sig_active = {s: np.zeros(N + 1, dtype=int) for s in ("cpu", "mem", "disk_root", "disk_data", "rx", "tx")}
        for pf in t.plan.faults:
            f = pf.sim
            from simulator_v2.footprints import footprint_signals
            observed = set()
            for sig, (mul, add) in f.eff.items():
                nz = (mul != 1.0) | (add != 0.0)
                if nz.any():
                    observed.add(sig)
                    sig_active[sig][f.start_i:f.conflict_end_i + 1] += nz.astype(int)
            assert observed <= footprint_signals(f.footprint_class), (f.family, f.footprint_class, observed)
            if f.panic_gap or f.reset_i is not None:
                assert f.footprint_class == "F10"                               # availability effect reserves ALL (FP6/FP11)
        assert all(a.max() <= 1 for a in sig_active.values())                   # no two faults non-zero on one signal at once


def test_concurrency_and_compatibility_hold_for_every_overlap_pair(planned):
    for t in planned:
        fs = [pf.sim for pf in t.plan.faults]
        for i, a in enumerate(fs):
            for b in fs[i + 1:]:
                if PL.overlaps(a.start_i, a.conflict_end_i, b.start_i, b.conflict_end_i):
                    assert MATRIX[a.footprint_class][b.footprint_class]
        for x in sorted({f.start_i for f in fs}):
            assert sum(1 for f in fs if f.start_i <= x < f.conflict_end_i) <= C.CONCURRENCY_LIMIT


def test_incident_faults_never_overlap_and_return_ramp_extends_some_masks(planned):
    for t in planned:
        inc = [pf.sim for pf in t.plan.faults if pf.role != "twin"]
        for i, a in enumerate(inc):
            for b in inc[i + 1:]:
                assert not PL.overlaps(a.start_i, a.conflict_end_i, b.start_i, b.conflict_end_i)       # FP9
    srcs = Counter(i["oracle_mask_end_source"] for t in planned for i in t.records["incidents"])
    assert srcs["return_ramp"] > 0 and srcs["cooldown"] > 0


def stub(cls, a, b):
    return SimpleNamespace(footprint_class=cls, start_i=a, conflict_end_i=b)


def pl(*sims):
    return [PlannedFault(s, "twin", "twin", 1, None) for s in sims]


def test_fp10_twins_obey_the_same_rules_inside_exclusion_spans_and_cooldown_tails():
    incident = stub("F9", 100, 300)                       # conflict interval; its mask/cooldown runs on to step 330
    # (a) conflicting footprint overlapping the incident's conflict interval is rejected even though the overlap is inside the mask
    assert not placement_ok(stub("F1", 250, 400), pl(incident))
    # (b) compatible footprint inside the exclusion span: accepted if the limit holds, rejected if it makes three concurrent faults
    assert placement_ok(stub("F2", 150, 250), pl(incident))
    assert not placement_ok(stub("F2", 150, 250), pl(incident, stub("F3", 120, 280)))
    # (c) entirely after conflict_end but inside the cooldown tail: accepted regardless of footprint
    assert placement_ok(stub("F9", 300, 500), pl(incident)) and placement_ok(stub("F10", 310, 500), pl(incident))
    # (d) one latent step earlier it overlaps conflict_end and is rejected when footprints conflict
    assert not placement_ok(stub("F9", 299, 500), pl(incident))


def test_plan_is_aborted_after_exactly_100_replans(monkeypatch, truth_cache):
    t = truth_cache(0)
    calls = []

    def always_reject(spec, ctx, streams, replan):
        calls.append(replan)
        raise PL.GroupRejected("forced")
    monkeypatch.setattr(PL, "_plan_once", always_reject)
    with pytest.raises(PL.PlanInfeasible, match="100 re-plans"):
        PL.plan_run(t.spec, t.profile, t.base, t.streams)
    assert calls == list(range(C.MAX_REPLANS))


# ---------------------------------------------------------------------------------------------- placement failure -> whole-run replan
from copy import copy

import numpy as np

from simulator_v2.alloc import RunSpec
from simulator_v2.faults.families import REDRAW_START, SIM, Ctx
from simulator_v2.rng import RunStreams


def _rng_of(k):
    return np.random.default_rng([k, 17])


def raised(base_edit, family, accept_extra=None, start=3000):
    t_mod = copy(base_edit[0]); ctx = Ctx(base_edit[1], t_mod)
    with pytest.raises(PL.PlacementInfeasible) as e:
        PL._place_incident(family, ctx, start, _rng_of, [], 20, accept_extra=accept_extra)
    return e.value


def host(truth_cache, **bumps):
    t = truth_cache(95)
    base = copy(t.base)
    for name, delta in bumps.items():
        setattr(base, name, getattr(t.base, name) + delta)
    return base, t.profile, t


def test_mem_leak_with_no_feasible_start_is_classified_start_domain_infeasible(truth_cache):
    base, prof, _ = host(truth_cache, mem=60.0)                 # noise-free memory above every kill level for the whole run
    err = raised((base, prof), "mem_leak")
    assert err.reason == PL.R_START_DOMAIN and err.detail["attempts"] == C.MAX_ATTEMPTS == 1000
    assert err.detail["precondition_redraw_start"] == 1000 and err.detail["outcome_mismatch"] == 0 and err.detail["start0"] == 3000


def test_disk_fill_u0_precondition_is_handled_the_same_way(truth_cache):
    base, prof, _ = host(truth_cache, disk_root=100.0, disk_data=100.0)       # usage already above 100 - r on either mount
    assert SIM["disk_fill"](Ctx(prof, base), 3000, "incident", _rng_of(1)) in (REDRAW_START, None)
    err = raised((base, prof), "disk_fill")
    assert err.reason == PL.R_START_DOMAIN and err.detail["precondition_redraw_start"] > 900


def test_attempts_exhausted_with_feasible_start_is_a_different_reason(truth_cache):
    base, prof, _ = host(truth_cache)
    err = raised((base, prof), "cpu_runaway", accept_extra=lambda f: False)
    assert err.reason == PL.R_ATTEMPTS and err.detail["rejected_by_caller"] > 0


def test_infeasible_primary_triggers_exactly_100_replans_then_aborts_with_structured_reasons(truth_cache):
    base, prof, t = host(truth_cache, mem=60.0)
    spec = RunSpec(95, "r0095", "val", "queue_consumer", False, [("mem_leak", False)])
    with pytest.raises(PL.PlanInfeasible) as e:
        PL.plan_run(spec, prof, base, RunStreams(0, 95))
    rej = e.value.rejections
    assert len(rej) == C.MAX_REPLANS == 100 and [r["replan"] for r in rej] == list(range(100))
    assert {r["reason"] for r in rej} == {PL.R_START_DOMAIN} and {r["family"] for r in rej} == {"mem_leak"}
    assert "100 re-plans" in str(e.value) and "start_domain_infeasible" in str(e.value)


def test_a_failing_plan_is_redrawn_and_no_primary_is_dropped(truth_cache):
    t = truth_cache(157)                                   # the run whose first plan has no feasible mem_leak start
    from collections import Counter
    assert t.plan.replans >= 1 and t.stats["rejections"] and t.stats["rejections"][0]["reason"] in (PL.R_START_DOMAIN, PL.R_ATTEMPTS)
    assert Counter(pf.sim.family for pf in t.plan.faults if pf.role == "primary") == Counter(f for f, _ in t.spec.primaries)
    for rej in t.stats["rejections"]:
        assert set(rej) >= {"replan", "reason", "detail"} and rej["replan"] < t.plan.replans


def test_rejections_are_recorded_for_every_replan_of_a_passing_run(planned):
    for t in planned:
        assert len(t.stats["rejections"]) == t.plan.replans
