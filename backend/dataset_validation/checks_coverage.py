"""Outcome and fault coverage, and the split definition.

Coverage rule (stated, not inferred): the outage-prediction evaluation reports PRECURSOR, ABRUPT and SILENT separately and per fault
family. So every (family, category) cell that the design is meant to produce must reach MIN_GROUP_EXAMPLES_EVAL incidents in each
evaluation split. SILENT is defined to be silent_crash only, so the non-silent families are required to produce PRECURSOR and ABRUPT.
"""
from __future__ import annotations

from collections import Counter

from . import policy as P
from .report import Report

NON_SILENT = tuple(f for f in P.FAMILIES if f not in P.SILENT_FAMILIES)


def check_coverage(incidents: list[dict], runs: dict, r: Report) -> None:
    r.check("coverage.outcome_and_fault_type")
    cell = Counter((runs[i["run_id"]]["split"], i["family"], i["group"]) for i in incidents)
    overall = Counter(i["group"] for i in incidents)
    stats = {}
    for s in P.EVALUATION_SPLITS:
        for g in P.GROUPS:
            n = sum(cell[(s, f, g)] for f in P.FAMILIES)
            stats[f"{s}/{g}"] = n
            if n < P.MIN_GROUP_EXAMPLES_EVAL:
                r.add("hard", "coverage", "coverage.category", f"{s} has {n} {g} incidents; at least {P.MIN_GROUP_EXAMPLES_EVAL} are needed")
        for f in NON_SILENT:
            for g in ("PRECURSOR", "ABRUPT"):
                n = cell[(s, f, g)]
                if n < P.MIN_GROUP_EXAMPLES_EVAL:
                    r.add("hard", "coverage", "coverage.family_category",
                          f"{s}: fault family {f} has {n} {g} incidents; at least {P.MIN_GROUP_EXAMPLES_EVAL} are needed to evaluate {g} for this family")
        for f in P.SILENT_FAMILIES:
            if sum(cell[(s, f, g)] for g in P.GROUPS) == 0:
                r.add("hard", "coverage", "coverage.family_missing", f"{s} has no {f} incidents")
    for g in P.GROUPS:
        if overall[g] == 0:
            r.add("hard", "coverage", "coverage.group_missing", f"no {g} incidents in the dataset")
    r.stats["coverage"] = {"by_split_group": stats,
                           "family_group_cells": {f"{s}/{f}/{g}": cell[(s, f, g)] for s in P.EVALUATION_SPLITS for f in P.FAMILIES for g in P.GROUPS}}


def check_split_definition(runs: dict, protocol: dict, master_seed: int, r: Report) -> dict:
    """The split is a partition of runs. Evaluation runs are whole runs, so no prediction window can straddle two partitions."""
    r.check("splits.run_level_partition")
    by_split = {s: sorted(rid for rid, run in runs.items() if run["split"] == s) for s in P.SPLITS}
    all_ids = [rid for ids in by_split.values() for rid in ids]
    if len(all_ids) != len(set(all_ids)):
        r.add("hard", "splits", "splits.overlap", "a run appears in more than one split", len(all_ids) - len(set(all_ids)))
    if set(all_ids) != set(runs):
        r.add("hard", "splits", "splits.unassigned", "a run is not assigned to a split")
    for s in P.SPLITS:
        expected = protocol["split_runs"][s]
        if len(by_split[s]) != expected:
            r.add("hard", "splits", "splits.count", f"{s} has {len(by_split[s])} runs; protocol requires {expected}")
    r.add("warn", "splits", "splits.single_seed",
          f"the dataset comes from one master seed ({master_seed}); there is no held-out seed. Held-out evaluation is across runs, not seeds")
    r.stats["splits"] = {s: len(v) for s, v in by_split.items()}
    return by_split


def assert_isolated(training_runs, evaluation_runs) -> None:
    """Contract for later ML code: training and evaluation run sets must be disjoint."""
    overlap = set(training_runs) & set(evaluation_runs)
    if overlap:
        raise ValueError(f"training and evaluation share runs: {sorted(overlap)[:5]}")
