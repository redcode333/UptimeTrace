"""FP1, FP2, FP6, FP7 (spec 4.5): exhaustive pairs, concurrency limit, availability class, boundaries."""
from __future__ import annotations

from itertools import product
from types import SimpleNamespace

import pytest

from simulator_v2 import footprints as F
from simulator_v2.planner import PlannedFault, overlaps, placement_ok

STEP = 1   # one latent step = 30 s


def stub(cls, start, end):
    return SimpleNamespace(footprint_class=cls, start_i=start, conflict_end_i=end)


def placed(*sims):
    return [PlannedFault(s, "twin", "twin", 1, None) for s in sims]


def test_matrix_equals_the_one_recomputed_from_signal_sets():
    for a, b in product(F.CLASSES, F.CLASSES):
        assert F.MATRIX[a][b] == F.compatible_from_signals(a, b), (a, b)
    assert all(F.MATRIX[a][b] == F.MATRIX[b][a] for a, b in product(F.CLASSES, F.CLASSES))


def test_there_are_exactly_18_compatible_unordered_pairs():
    assert len(F.COMPAT_PAIRS) == 18
    assert sum(1 for b in F.CLASSES if b != "F1" and F.MATRIX["F1"][b]) == 7
    partners = lambda a: sorted(b for b in F.CLASSES if b != a and F.MATRIX[a][b])
    assert partners("F1") == ["F2", "F3", "F4", "F5", "F6", "F7", "F8"]
    assert [b for b in partners("F2") if b != "F1"] == ["F3", "F4", "F5", "F7", "F8", "F9"]
    assert [b for b in partners("F3") if b not in ("F1", "F2")] == ["F4", "F5", "F6", "F8", "F9"]
    assert F.COMPAT_PAIRS == tuple(sorted(F.COMPAT_PAIRS))


@pytest.mark.parametrize("a,b", list(product(F.CLASSES, F.CLASSES)))
def test_fp1_planner_accepts_overlap_by_30s_iff_matrix_says_compatible(a, b):
    existing = placed(stub(a, 100, 200))
    candidate = stub(b, 200 - STEP, 300)            # overlaps by exactly one latent step
    assert placement_ok(candidate, existing) == F.MATRIX[a][b]


def test_fp2_limit_of_two_rejects_a_third_concurrent_fault():
    f1, f2, f4 = stub("F1", 100, 300), stub("F2", 100, 300), stub("F4", 150, 250)
    assert F.MATRIX["F1"]["F2"] and F.MATRIX["F1"]["F4"] and F.MATRIX["F2"]["F4"]
    assert placement_ok(f2, placed(f1))
    assert placement_ok(f4, placed(f1))
    assert not placement_ok(f4, placed(f1, f2))     # pairwise compatible, but three at once


def test_fp2_third_fault_allowed_when_it_does_not_overlap_both():
    f1, f2 = stub("F1", 100, 200), stub("F2", 190, 400)
    assert placement_ok(stub("F4", 250, 350), placed(f1, f2))      # overlaps only F2


def test_fp6_panic_class_conflicts_with_every_class_both_ways():
    for c in F.CLASSES:
        assert not F.MATRIX["F10"][c] and not F.MATRIX[c]["F10"]
        assert not placement_ok(stub("F10", 150, 250), placed(stub(c, 100, 200)))
        assert not placement_ok(stub(c, 150, 250), placed(stub("F10", 100, 200)))
    assert F.CLASS_SIGNALS["F10"] == F.ALL


def test_fp7_touching_intervals_are_accepted_even_for_conflicting_footprints():
    assert not F.MATRIX["F5"]["F5"]
    assert placement_ok(stub("F5", 200, 300), placed(stub("F5", 100, 200)))       # end_a == start_b
    assert placement_ok(stub("F5", 0, 100), placed(stub("F5", 100, 200)))
    assert not placement_ok(stub("F5", 199, 300), placed(stub("F5", 100, 200)))   # overlap by one step
    assert overlaps(100, 200, 200, 300) is False and overlaps(100, 201, 200, 300) is True


def test_footprint_class_mapping_follows_the_audit_table():
    fc = F.footprint_class
    assert fc("mem_leak", "twin") == "F1" and fc("mem_leak", "incident") == "F9"
    assert fc("mem_leak", "incident", panic=True) == "F10"
    assert fc("disk_fill", "twin", mount="/") == "F2" and fc("disk_fill", "twin", mount="/data") == "F3"
    assert fc("disk_fill", "incident", mount="/") == "F6" and fc("disk_fill", "incident", mount="/data") == "F7"
    assert fc("cpu_runaway", "twin") == "F4" and fc("cpu_runaway", "incident") == "F5"
    assert fc("net_flood", "twin") == fc("net_flood", "incident") == "F8"
    assert fc("pool_exhaustion", "twin") == fc("pool_exhaustion", "incident") == "F9"
    assert [fc("silent_crash", "incident", subtype=s) for s in ("process_crash", "dependency_outage", "kernel_panic")] == ["F9", "F8", "F10"]
