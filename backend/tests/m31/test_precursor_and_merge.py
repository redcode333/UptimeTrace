"""AC-11 precursor tests T1-T8 (spec 7.4) and AC-06 / AC-08 merging, oracle-mask end (spec 6, 13)."""
from __future__ import annotations

from types import SimpleNamespace

import numpy as np
import pytest

from simulator_v2 import constants as C
from simulator_v2.merge import is_excluded_active, merge_incidents
from simulator_v2.precursor import derive_precursor, signal_run_start

PROFILE = SimpleNamespace(sigma_marg={"cpu": 1.0, "mem": 1.0, "disk_root": 1.0, "disk_data": 1.0, "rx": 0.1, "tx": 0.1})


def fault(delta_cpu, t_ref_k, family="cpu_runaway", incident=True, start_i=1000):
    d = np.asarray(delta_cpu, dtype=float)
    f = SimpleNamespace(family=family, start_i=start_i, delta={"cpu": d})
    if incident:
        f.impact_start_i = start_i + t_ref_k
    else:
        f.peak_i = start_i + t_ref_k
    return f


def run_of(n_exceed, ends_at_ref=True, total=40, ref=20):
    """delta array whose exceeding run (|d| >= 2 sigma) has n_exceed points and contains `ref` (as last or first point)."""
    d = np.zeros(total)
    if ends_at_ref:
        d[ref - n_exceed + 1:ref + 1] = 5.0
    else:
        d[ref:ref + n_exceed] = 5.0
    return d, ref


def test_t1_one_point_run_gives_null():
    d, ref = run_of(1)
    info = derive_precursor(fault(d, ref), PROFILE, True)
    assert info.precursor_start_i is None and info.group == "SILENT" and info.silent_reason == "sub_noise"


def test_t2_two_point_run_30s_gives_null():
    d, ref = run_of(2)
    assert derive_precursor(fault(d, ref), PROFILE, True).precursor_start_i is None


def test_t3_three_point_run_60s_qualifies_with_lead_60_abrupt():
    d, ref = run_of(3)
    info = derive_precursor(fault(d, ref), PROFILE, True)
    assert info.precursor_start_i == 1000 + ref - 2 and info.lead_s == 60 and info.group == "ABRUPT" and info.signal == "cpu"


def test_t4_four_point_run_90s_lead_90_or_0():
    d, ref = run_of(4, ends_at_ref=True)
    info = derive_precursor(fault(d, ref), PROFILE, True)
    assert info.lead_s == 90 and info.group == "ABRUPT"
    d2, ref2 = run_of(4, ends_at_ref=False)                 # T_ref is the first point, run extends through the impact
    info2 = derive_precursor(fault(d2, ref2), PROFILE, True)
    assert info2.lead_s == 0 and info2.group == "ABRUPT"


def test_t5_lead_90_is_abrupt_and_lead_120_is_precursor():
    d90, r = run_of(4)
    d120, r2 = run_of(5)
    assert derive_precursor(fault(d90, r), PROFILE, True).group == "ABRUPT"
    i120 = derive_precursor(fault(d120, r2), PROFILE, True)
    assert i120.lead_s == 120 and i120.group == "PRECURSOR"


def test_t6_a_single_non_exceeding_point_before_t_ref_restarts_the_run():
    d = np.zeros(40)
    d[10:16] = 5.0                  # six points, then a gap at 16 (T_ref - 4)...
    d[17:21] = 5.0                  # run restarts at 17 and contains T_ref = 20: 4 points
    d[18] = 0.0                     # a non-exceeding point at T_ref - 2 restarts it again -> points 19..20 only
    info = derive_precursor(fault(d, 20), PROFILE, True)
    assert info.precursor_start_i is None                    # a 2 point run (19, 20) does not qualify
    assert signal_run_start(d, 1.0, 20) is None


def test_t7_exactly_two_sigma_counts_as_exceeding():
    d = np.zeros(40)
    d[18:21] = 2.0 * PROFILE.sigma_marg["cpu"]
    assert derive_precursor(fault(d, 20), PROFILE, True).precursor_start_i == 1018
    d[18:21] = np.nextafter(2.0, 0)
    assert derive_precursor(fault(d, 20), PROFILE, True).precursor_start_i is None


def test_t8_bit_identical_and_independent_of_noise():
    d, ref = run_of(8)
    a = derive_precursor(fault(d, ref), PROFILE, True)
    b = derive_precursor(fault(d.copy(), ref), PROFILE, True)
    assert a == b                                           # no noise stream is consulted at all


def test_properties_sigma_scaling_ties_twins_and_silent_crash():
    d, ref = run_of(8)
    s1 = derive_precursor(fault(d, ref), PROFILE, True).precursor_start_i
    big = SimpleNamespace(sigma_marg={**PROFILE.sigma_marg, "cpu": 2.4})
    s2 = derive_precursor(fault(d, ref), big, True).precursor_start_i
    assert s2 is None or s2 >= s1                           # raising sigma never makes precursor_start earlier
    scaled = derive_precursor(fault(d * 3, ref), SimpleNamespace(sigma_marg={**PROFILE.sigma_marg, "cpu": 3.0}), True)
    assert scaled.precursor_start_i == s1                   # scaling delta and sigma together leaves it unchanged
    twin = derive_precursor(fault(d, ref, incident=False), PROFILE, False)
    assert twin.precursor_start_i == s1 and twin.group is None          # twins use peak_time
    sc = derive_precursor(SimpleNamespace(family="silent_crash", start_i=0, delta={}), PROFILE, True)
    assert sc.group == "SILENT" and sc.silent_reason == "by_construction"
    # tie order: memory before disk before cpu before rx before tx
    f = SimpleNamespace(family="pool_exhaustion", start_i=1000, impact_start_i=1020,
                        delta={"cpu": run_of(6)[0], "mem": run_of(6)[0], "tx": run_of(6)[0] * 0.2})
    f.delta["tx"] = np.where(f.delta["tx"] > 0, 5.0, 0.0)
    info = derive_precursor(f, PROFILE, True)
    assert info.signal == "mem" and set(info.by_signal) == {"cpu", "mem", "tx"}


def test_group_is_a_function_of_lead_and_nullness():
    for n in range(1, 12):
        d, ref = run_of(n)
        info = derive_precursor(fault(d, ref), PROFILE, True)
        if n < 3:
            assert info.group == "SILENT"
        else:
            lead = (n - 1) * 30
            assert info.lead_s == lead and info.group == ("PRECURSOR" if lead >= 120 else "ABRUPT")


# ------------------------------------------------------------------------------------------------ merging (AC-06/08)
def inc(fid, start, end, conf_end=None, rel="primary", fam="cpu_runaway"):
    return {"fault_id": fid, "family": fam, "member_relation": rel, "impact_start": start, "impact_end": end,
            "conflict_end": conf_end if conf_end is not None else end + 600, "group": "ABRUPT", "silent_reason": None,
            "precursor_lead_s": 0, "severity": 0.5}


def t(h, m, s=0):
    return h * 3600 + m * 60 + s


def test_ac08_join_at_899s_no_join_at_900s_and_chain_of_three():
    a = inc("A", 0, 100)
    assert len(merge_incidents([a, inc("B", 100 + 899, 5000, rel="relapse")])) == 1
    assert len(merge_incidents([a, inc("B", 100 + 900, 5000, rel="relapse")])) == 2
    chain = [a, inc("B", 100 + 899, 1100, rel="relapse"), inc("C", 1100 + 899, 2100, rel="relapse")]
    m = merge_incidents(chain)
    assert len(m) == 1 and m[0]["n_members"] == 3 and m[0]["recovery"] == 2100 and m[0]["cooldown_end"] == 3000


def test_section6_worked_example_and_return_ramp_extension():
    A = inc("A", t(10, 0), t(10, 6), conf_end=t(10, 6) + 600)
    B = inc("B", t(10, 16), t(10, 36), rel="relapse", conf_end=t(10, 36) + 600)
    C_ = inc("C", t(10, 50, 30), t(10, 56), rel="relapse", conf_end=t(10, 56) + 18 * 60)       # T_ret = 18 min
    D = inc("D", t(11, 11), t(11, 20), conf_end=t(11, 20) + 600)
    m = merge_incidents([D, C_, A, B])
    assert len(m) == 2
    i1, i2 = m
    assert i1["onset"] == t(10, 0) and i1["recovery"] == t(10, 56) and i1["cooldown_end"] == t(11, 11)
    assert i1["oracle_mask_end"] == t(11, 14) and i1["oracle_mask_end_source"] == "return_ramp"
    assert i2["onset"] == t(11, 11) and i2["oracle_mask_end"] == t(11, 20) + 900 and i2["oracle_mask_end_source"] == "cooldown"
    # merging, cooldown and recovery do not depend on T_ret
    C2 = dict(C_, conflict_end=t(10, 56) + 5 * 60)
    m2 = merge_incidents([D, C2, A, B])
    for k in ("onset", "recovery", "cooldown_end", "members", "n_members"):
        assert [x[k] if k != "members" else [(y["fault_id"], y["impact_start"]) for y in x[k]] for x in m] == \
               [x[k] if k != "members" else [(y["fault_id"], y["impact_start"]) for y in x[k]] for x in m2]
    assert m2[0]["oracle_mask_end"] == t(11, 11) and m2[0]["oracle_mask_end_source"] == "cooldown"


def test_mask_end_ties_and_relapse_ramp_can_set_the_end():
    p = inc("P", 0, 600, conf_end=600 + 1200)
    r = inc("R", 700, 1000, rel="relapse", conf_end=1000 + 300)
    m = merge_incidents([p, r])[0]
    assert m["cooldown_end"] == 1900 and m["oracle_mask_end"] == 1900 and m["oracle_mask_end_source"] == "cooldown"   # tie -> cooldown
    p2 = inc("P", 0, 600, conf_end=600 + 100)
    r2 = inc("R", 700, 1000, rel="relapse", conf_end=1000 + 1200)
    m2 = merge_incidents([p2, r2])[0]
    assert m2["oracle_mask_end"] == 2200 and m2["oracle_mask_end_source"] == "return_ramp"     # relapse ramp, not the primary's


def test_mask_boundaries_are_left_closed_right_open_and_union():
    m = merge_incidents([inc("A", 1000, 1200, conf_end=1200 + 1100)])
    end = m[0]["oracle_mask_end"]
    assert end == 2300 and m[0]["cooldown_end"] == 2100
    assert is_excluded_active(1000, m) and not is_excluded_active(999, m)
    assert is_excluded_active(2299, m) and not is_excluded_active(2300, m)
    assert is_excluded_active(2100, m) and is_excluded_active(2200, m)         # cooldown_end <= t < conflict_end is masked
    two = merge_incidents([inc("A", 0, 100, conf_end=1500), inc("B", 100 + 900, 3000)])
    assert len(two) == 2 and two[0]["oracle_mask_end"] > two[1]["onset"]       # overlapping masks: exclusion is the union
    assert is_excluded_active(1100, two) and is_excluded_active(1499, two)
