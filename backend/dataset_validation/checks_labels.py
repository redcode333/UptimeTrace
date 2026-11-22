"""Derived labels, checked against the ground truth. Every target is recomputed from incidents.jsonl and compared row by row.

Definition under test (labels manifest): target = 1 iff a merged incident onset lies in (t, t+H]; decisions with t in an incident's
oracle mask [onset, oracle_mask_end) are excluded; rows failing the data-sufficiency rule are excluded. Decisions are at
run_start + DECISION_OFFSET_S + i * DECISION_DELTA_S for i = 0 .. DECISIONS_PER_RUN - 1.
"""
from __future__ import annotations

from collections import Counter
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

from . import policy as P
from .reading import try_read
from .timeutil import epoch_seconds
from .report import Report

EXCLUSIONS = {"active_incident", "insufficient_data"}
CLASSES = {"positive", "negative", "excluded", "unscorable"}
# A decision inside an oracle mask may be excluded for either reason (the labeler applies a precedence between them), so the
# exclusion reason is not compared exactly. What is compared: no masked decision is scored, and every "active_incident" label lies in a mask.


def _ts(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def _run_truth(run_id: str, start: datetime, incidents: list[dict]):
    """Onsets (seconds since run start), oracle-mask ends, and the family of each onset, for one run."""
    rows = sorted(((_ts(i["onset"]) - start).total_seconds(), (_ts(i["oracle_mask_end"]) - start).total_seconds(), i["family"], i["incident_id"])
                  for i in incidents if i["run_id"] == run_id)
    onsets = np.array([r[0] for r in rows], dtype="float64")
    ends = np.array([r[1] for r in rows], dtype="float64")
    fams = [r[2] for r in rows]
    return onsets, ends, fams


def check_labels(labels_dir: Path, runs: dict, incidents: list[dict], r: Report) -> dict:
    r.check("labels.recomputed_from_ground_truth")
    files = sorted(labels_dir.rglob("*.parquet"))
    expected_files = len(runs) * len(P.HORIZONS)
    if len(files) != expected_files:
        r.add("hard", "labels", "labels.file_count", f"expected {expected_files} label files (runs x horizons), found {len(files)}")
    seen = Counter()
    by_run_truth = {}
    target_mismatch, active_mismatch, class_mismatch, null_mismatch, time_mismatch, split_mismatch, count_mismatch, next_mismatch = ([] for _ in range(8))
    exclusion_values = Counter()
    positives = Counter()           # (split, horizon) -> positive rows
    unscorable_total = Counter()    # (split, horizon) -> rows classed "unscorable"
    unscorable_as_positive = Counter()   # those rows whose target is 1
    unscorable_runs: list[str] = []
    operational_mismatch: list[str] = []
    schema_missing: list[str] = []
    scored = Counter()              # (split, horizon) -> scored rows
    family_positive = Counter()     # (split, horizon, family) -> positive rows whose next incident is that family
    stats_rows = 0
    for path in files:
        parts = {p.split("=", 1)[0]: p.split("=", 1)[1] for p in path.parts if "=" in p}
        horizon = int(parts["horizon_s"])
        run_id = path.stem.split("=", 1)[1]
        seen[(run_id, horizon)] += 1
        if run_id not in runs:
            split_mismatch.append(run_id)
            continue
        run = runs[run_id]
        if parts["split"] != run["split"]:
            split_mismatch.append(run_id)
        start = _ts(run["start_ts"])
        if run_id not in by_run_truth:
            by_run_truth[run_id] = _run_truth(run_id, start, incidents)
        onsets, ends, fams = by_run_truth[run_id]
        t = try_read(path, None, r, "labels", run_id)
        if t is None:
            continue
        stats_rows += len(t)
        if len(t) != P.DECISIONS_PER_RUN or sorted(t["decision_index"]) != list(range(P.DECISIONS_PER_RUN)):
            count_mismatch.append(run_id)
            continue
        t = t.sort_values("decision_index").reset_index(drop=True)
        dts = epoch_seconds(t["decision_ts"])
        rel = np.arange(P.DECISIONS_PER_RUN, dtype="float64") * P.DECISION_DELTA_S + P.DECISION_OFFSET_S
        start_epoch = start.timestamp()
        if not np.allclose(dts - start_epoch, rel, atol=1e-3):
            time_mismatch.append(run_id)
        if (t["horizon_s"] != horizon).any() or (t["split"] != parts["split"]).any():
            split_mismatch.append(run_id)
        # Recomputed truth at every decision
        nxt_idx = np.searchsorted(onsets, rel, side="right")
        has_next = nxt_idx < len(onsets)
        padded = np.append(onsets, np.inf)                  # index len(onsets) means: no later onset
        next_onset = padded[nxt_idx]
        target_truth = (next_onset - rel <= horizon) & has_next
        active_truth = np.zeros(P.DECISIONS_PER_RUN, dtype=bool)
        for a, b in zip(onsets, ends):
            active_truth |= (rel >= a) & (rel < b)
        excl = t["exclusion"].astype(object).where(t["exclusion"].notna(), None)
        for v in excl:
            exclusion_values[v] += 1
        is_active_label = excl.to_numpy() == "active_incident"
        if (is_active_label & ~active_truth).any():
            active_mismatch.append(run_id)
        scored_mask = t["scored"].to_numpy(dtype=bool)
        cls = t["label_class"].astype(str).to_numpy()
        if not set(np.unique(cls)).issubset(CLASSES):
            class_mismatch.append(run_id)
        # Protocol semantics (ML evaluation protocol, "decision_point_status"):
        #   evaluable  = not excluded (features exist, alerts and exposure count);
        #   PRIMARY    scored = positive or negative only; unscorable is evaluable but never scored.
        if "evaluable" not in t.columns or "operational_target" not in t.columns:
            schema_missing.append(run_id)
            continue
        evaluable = t["evaluable"].to_numpy(dtype=bool)
        if (evaluable != (cls != "excluded")).any():
            class_mismatch.append(run_id)
        if (scored_mask != np.isin(cls, ["positive", "negative"])).any():
            class_mismatch.append(run_id)
        op = t["operational_target"].to_numpy(dtype="float64")
        if (np.isnan(op[evaluable]).any()
                or (op[cls == "positive"] != 1).any() or (op[cls == "unscorable"] != 1).any()
                or (op[cls == "negative"] != 0).any() or np.isfinite(op[~evaluable]).any()):
            operational_mismatch.append(run_id)
        if ((cls == "positive") & (np.nan_to_num(t["target"].to_numpy(dtype="float64"), nan=-1) != 1)).any() or \
                ((cls == "negative") & (np.nan_to_num(t["target"].to_numpy(dtype="float64"), nan=-1) != 0)).any():
            class_mismatch.append(run_id)
        unscorable_rows = int((cls == "unscorable").sum())
        unscorable_scored = int(((cls == "unscorable") & scored_mask).sum())
        unscorable_total[(parts["split"], horizon)] += unscorable_rows
        unscorable_as_positive[(parts["split"], horizon)] += unscorable_scored
        if unscorable_scored:
            unscorable_runs.append(run_id)
        masked_scored = int((scored_mask & active_truth).sum())
        if masked_scored:
            active_mismatch.append(run_id)
        target = t["target"].to_numpy(dtype="float64")
        if (np.isnan(target) == scored_mask).any():
            null_mismatch.append(run_id)
        sc = scored_mask & ~active_truth
        if (target[sc] != target_truth[sc].astype(float)).any():
            target_mismatch.append(run_id)
        pos = scored_mask & (target == 1)
        positives[(parts["split"], horizon)] += int(pos.sum())
        scored[(parts["split"], horizon)] += int(scored_mask.sum())
        if horizon == P.PRIMARY_HORIZON_S:
            idx = np.where(pos)[0]
            for k in idx:
                j = nxt_idx[k]
                if j < len(fams):
                    family_positive[(parts["split"], horizon, fams[j])] += 1
        nxt_label = t["next_onset_s"].to_numpy(dtype="float64")
        recomputed = np.where(has_next, next_onset - rel, np.nan)
        both = ~np.isnan(nxt_label) & ~np.isnan(recomputed)
        if (np.abs(nxt_label[both] - recomputed[both]) > 1.0).any() or (np.isnan(nxt_label) != np.isnan(recomputed)).any():
            next_mismatch.append(run_id)
    missing = [f"{rid}@{h}" for rid in runs for h in P.HORIZONS if seen[(rid, h)] == 0]
    dupes = [f"{rid}@{h}" for (rid, h), n in seen.items() if n > 1]
    if missing:
        r.add("hard", "labels", "labels.missing", "run/horizon has no label file", len(missing), [m.split("@")[0] for m in missing])
    if dupes:
        r.add("hard", "labels", "labels.duplicate", "run/horizon has two label files", len(dupes), [d.split("@")[0] for d in dupes])
    if split_mismatch:
        r.add("hard", "labels", "labels.split", "label split or horizon column disagrees with the run's split", len(set(split_mismatch)), split_mismatch)
    if count_mismatch:
        r.add("hard", "labels", "labels.decision_count", f"a run does not have {P.DECISIONS_PER_RUN} decisions with indices 0..N-1", len(set(count_mismatch)), count_mismatch)
    if time_mismatch:
        r.add("hard", "labels", "labels.decision_time", "decision times are not run_start + 5 s + 60 s * i", len(set(time_mismatch)), time_mismatch)
    if target_mismatch:
        r.add("hard", "labels", "labels.target_mismatch", "stored target disagrees with the ground-truth recomputation", len(set(target_mismatch)), target_mismatch)
    if active_mismatch:
        r.add("hard", "labels", "labels.active_incident_exclusion", "oracle-masked decisions are not exactly the excluded active-incident rows", len(set(active_mismatch)), active_mismatch)
    if class_mismatch:
        r.add("hard", "labels", "labels.class", "label_class, scored and target disagree", len(set(class_mismatch)), class_mismatch)
    if sum(unscorable_as_positive.values()):
        r.add("hard", "labels", "labels.unscorable_scored",
              "unscorable decisions are inside the primary scored view; they must be masked from it", sum(unscorable_as_positive.values()),
              sorted(set(unscorable_runs)))
    if operational_mismatch:
        r.add("hard", "labels", "labels.operational_semantics",
              "operational_target does not match the secondary view (1 for positive and unscorable, 0 for negative, null when excluded)",
              len(set(operational_mismatch)), operational_mismatch)
    if schema_missing:
        r.add("hard", "labels", "labels.schema", "label files lack the evaluable or operational_target column", len(set(schema_missing)),
              schema_missing)
    if null_mismatch:
        r.add("hard", "labels", "labels.null_target", "target is null on a scored row, or present on an unscored row", len(set(null_mismatch)), null_mismatch)
    if next_mismatch:
        r.add("hard", "labels", "labels.next_onset", "next_onset_s disagrees with the ground truth", len(set(next_mismatch)), next_mismatch)
    bad_excl = set(exclusion_values) - EXCLUSIONS - {None}
    if bad_excl:
        r.add("hard", "labels", "labels.exclusion_value", f"unknown exclusion values: {sorted(map(str, bad_excl))}")
    stats = {"files": len(files), "rows": stats_rows,
             "scored_by_split_horizon": {f"{s}@{h}": scored[(s, h)] for s in P.SPLITS for h in P.HORIZONS},
             "positives_by_split_horizon": {f"{s}@{h}": positives[(s, h)] for s in P.SPLITS for h in P.HORIZONS},
             "exclusions": {str(k): v for k, v in exclusion_values.items()},
             "unscorable_by_split_horizon": {f"{s}@{h}": unscorable_total[(s, h)] for s in P.SPLITS for h in P.HORIZONS},
             "unscorable_positive_by_split_horizon": {f"{s}@{h}": unscorable_as_positive[(s, h)] for s in P.SPLITS for h in P.HORIZONS},
             "primary_positives_by_split_family": {f"{s}/{f}": family_positive[(s, P.PRIMARY_HORIZON_S, f)]
                                                   for s in P.SPLITS for f in P.FAMILIES}}
    r.stats["labels"] = stats
    return stats


def check_primary_horizon(stats: dict, r: Report) -> None:
    """Can the 30-minute outage prediction be evaluated? Each evaluation split needs both classes at the primary horizon."""
    r.check("labels.primary_horizon_evaluable")
    for s in P.EVALUATION_SPLITS:
        pos = stats["positives_by_split_horizon"][f"{s}@{P.PRIMARY_HORIZON_S}"]
        neg = stats["scored_by_split_horizon"][f"{s}@{P.PRIMARY_HORIZON_S}"] - pos
        if pos == 0 or neg == 0:
            r.add("hard", "labels", "labels.primary_horizon_classes", f"{s} at {P.PRIMARY_HORIZON_S} s lacks one class", pos)
        if pos < 100:
            r.add("warn", "labels", "labels.primary_positives_low", f"{s} has only {pos} positive rows at the primary horizon")
