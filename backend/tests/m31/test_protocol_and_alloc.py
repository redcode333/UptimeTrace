"""AC-30 (protocol hash), AC-05 allocation (quotas, caps, relapses, archetypes) against spec section 2, 5, 10."""
from __future__ import annotations

import copy
from collections import Counter

import pytest

from simulator_v2 import constants as C
from simulator_v2.alloc import allocate
from simulator_v2.protocol import build_protocol, canonical_json, protocol_hash


def test_spec_constants_match_the_specification():
    assert C.HORIZONS == (900, 1800, 3600) and C.L_MIN == 120 and C.COOLDOWN == C.MERGE_WINDOW == 900
    assert C.PLANNER_WARMUP == 7200 and C.CONCURRENCY_LIMIT == 2 and C.ALERT_DEDUP == 900
    assert C.J_MAX == 2.0 and C.S_MAX == 1.5 and C.D_MAX == 1800 and C.REPLAY_DELAY == (0.5, 5.0)
    assert C.RUN_DAYS == 14 and C.N_STEPS == 40320 and C.CADENCE_DEFAULT == 60
    assert {s: sum(C.QUOTAS[f][s] for f in C.FAMILIES) for s in C.SPLITS} == {"train": 650, "val": 195, "test_iid": 293, "test_ood": 162}
    assert sum(sum(v.values()) for v in C.QUOTAS.values()) == 1300
    assert sum(C.SECONDARIES.values()) == 129 and C.SPLIT_RUNS == {"train": 90, "val": 30, "test_iid": 40, "test_ood": 20}
    assert [C.SECONDARIES[s] for s in C.SPLITS] == [int(0.10 * sum(C.QUOTAS[f][s] for f in C.FAMILIES)) for s in C.SPLITS]   # floor(0.10 x primaries)


def test_protocol_has_every_frozen_field_and_version_4_5():
    p = build_protocol()
    assert p["protocol_version"] == "4.5"
    assert p["sampling_origin"] == "run_start" and p["buffered_membership"] == "true_sample_time"
    assert p["oracle_mask"] == {"start": "onset", "end": "max(cooldown_end, max_member_conflict_end)",
                                "interval": "left_closed_right_open", "cooldown_s": 900,
                                "members": "incident_faults_primary_and_relapse"}
    assert p["availability_bounds_s"]["buffered"] == {"lag_vs_obs_ts": 1805.0, "lag_vs_nominal": 1808.5}
    assert p["availability_bounds_s"]["normal"] == {"lag_vs_obs_ts": 0.4, "lag_vs_nominal": 3.9}
    assert len(p["footprints"]["classes"]) == 10 and p["false_alarm"]["version"] == "fa-2"
    assert p["counter_reset_policy"]["series"] == ["rx", "tx"]


@pytest.mark.parametrize("path,newval", [
    (("oracle_mask", "end"), "cooldown_end"),
    (("sampling_origin",), "nominal_grid"),
    (("buffered_membership",), "nominal_time"),
    (("protocol_version",), "4.3"),
    (("constants", "cooldown_s"), 1000),
    (("constants", "j_max_s"), 2.5),
    (("constants", "d_max_s"), 3600),
    (("availability_bounds_s", "buffered", "lag_vs_nominal"), 1806.5),
    (("footprints", "compatibility_matrix", "F1", "F2"), False),
    (("counter_reset_policy", "sources"), ["planned_reboot_at_gap_end"]),
    (("false_alarm", "version"), "fa-1"),
    (("quotas", "mem_leak", "train"), 99),
])
def test_changing_any_frozen_field_changes_the_hash(path, newval):
    base = build_protocol()
    h0 = protocol_hash(base)
    mod = copy.deepcopy(base)
    node = mod
    for k in path[:-1]:
        node = node[k]
    assert node[path[-1]] != newval
    node[path[-1]] = newval
    assert protocol_hash(mod) != h0


def test_hash_is_independent_of_key_order_and_stable():
    p = build_protocol()
    reordered = dict(reversed(list(p.items())))
    assert canonical_json(p) == canonical_json(reordered)
    assert protocol_hash(p) == protocol_hash(build_protocol())


@pytest.mark.parametrize("seed", [0, 1, 2, 3, 4])
def test_allocation_meets_exact_quotas_cap_and_relapse_counts(seed):
    runs, doc = allocate(seed)
    assert len(runs) == 180 and Counter(r.split for r in runs) == Counter(C.SPLIT_RUNS)
    got = Counter((r.split, f) for r in runs for f, _ in r.primaries)
    assert all(got[(s, f)] == C.QUOTAS[f][s] for f in C.FAMILIES for s in C.SPLITS)
    assert max(len(r.primaries) for r in runs) <= C.PRIMARY_CAP_PER_RUN
    rel = Counter(r.split for r in runs for _, g in r.primaries if g)
    assert dict(rel) == C.SECONDARIES
    assert all(f in C.RELAPSE_FAMILIES for r in runs for f, g in r.primaries if g)      # at most one relapse each by construction
    assert len({r.run_id for r in runs}) == 180


def test_allocation_is_deterministic_and_seed_dependent():
    a, b, c = allocate(0)[0], allocate(0)[0], allocate(1)[0]
    assert [(r.archetype, r.primaries) for r in a] == [(r.archetype, r.primaries) for r in b]
    assert [(r.archetype, r.primaries) for r in a] != [(r.archetype, r.primaries) for r in c]


def test_archetype_balance_and_explicit_ood_requirements():
    runs, doc = allocate(0)
    for split in ("train", "val", "test_iid"):
        cnt = Counter(r.archetype for r in runs if r.split == split)
        assert set(cnt) == set(C.ARCHETYPES) and max(cnt.values()) - min(cnt.values()) <= 1
        assert not any(r.ood_slow for r in runs if r.split == split)
    ood = [r for r in runs if r.split == "test_ood"]
    assert sum(r.archetype == "cache_node" for r in ood) == 10 and not any(r.ood_slow for r in ood if r.archetype == "cache_node")
    known = [r for r in ood if r.archetype != "cache_node"]
    assert len(known) == 10 and all(r.ood_slow for r in known)
    kc = Counter(r.archetype for r in known)
    assert max(kc.values()) - min(kc.values()) <= 1
    assert "rule" in doc and set(doc["archetype_counts"]) == set(C.SPLITS)
