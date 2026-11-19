"""Read-only: the counts that docs/ML_EVALUATION_PROTOCOL.md quotes (split membership, exposure, incidents by group and family, credit-window evaluability).

    python -I scripts/protocol_expected_counts.py --out docs/evidence/ml_evaluation_protocol_expected_counts.json [--repo <root>] [--dataset <id>]

Reads runs.jsonl, incidents.jsonl and the H = 1800 label files (only the `decision_ts` and `scored` columns). It does not import the production
labeler, trains nothing and writes only the JSON report. Deterministic.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter, defaultdict
from datetime import UTC, datetime, timedelta
from pathlib import Path

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq

EPOCH = datetime(1970, 1, 1, tzinfo=UTC)
US = 1_000_000
SPLITS = ("train", "val", "test_iid", "test_ood")
H_S, L_MIN_S, DELTA_S = 1800, 120, 60
FAMILIES = ("mem_leak", "disk_fill", "cpu_runaway", "net_flood", "pool_exhaustion")


def us(ts: str) -> int:
    return (datetime.fromisoformat(ts) - EPOCH) // timedelta(microseconds=1)


def jl(p: Path) -> list[dict]:
    with open(p, encoding="utf-8") as f:
        return [json.loads(x) for x in f if x.strip()]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--repo", default=str(Path(__file__).resolve().parents[1]))
    ap.add_argument("--dataset", default="uptimetrace-m31-seed0-2723fc6789d8")
    a = ap.parse_args()
    repo = Path(a.repo)
    root = repo / "artifacts" / "datasets" / a.dataset
    lab = repo / "artifacts" / "labels_full" / a.dataset / "labels"
    runs = jl(root / "runs.jsonl")
    incs = jl(root / "incidents.jsonl")
    by_run = defaultdict(list)
    for i in incs:
        by_run[i["run_id"]].append(i)
    sub_of = {}
    for r in runs:
        sub_of[r["run_id"]] = ("ood_slow" if r["profile"]["ood_slow"] else "ood_cache_node") if r["split"] == "test_ood" else r["split"]
    members = {s: sorted(r["run_id"] for r in runs if r["split"] == s) for s in SPLITS}
    members["test_ood_slow"] = sorted(r for r in members["test_ood"] if sub_of[r] == "ood_slow")
    members["test_ood_cache_node"] = sorted(r for r in members["test_ood"] if sub_of[r] == "ood_cache_node")
    acc = {k: {"runs": 0, "scored_rows": 0, "insufficient_or_active_rows": 0, "incidents": Counter(), "precursor_by_family": Counter(),
               "abrupt_by_family": Counter(), "silent_by_family": Counter(), "precursor_without_eligible_scored_decision": Counter(),
               "precursor_eligible_points": []} for k in members}
    for key, rids in members.items():
        for rid in rids:
            r = next(x for x in runs if x["run_id"] == rid)
            start, end = us(r["start_ts"]), us(r["end_ts"])
            f = pq.ParquetFile(lab / f"split={r['split']}" / "horizon_s=1800" / f"run={rid}.parquet").read(columns=["decision_ts", "scored"])
            t = f.column("decision_ts").cast(pa.int64()).to_numpy()
            sc = f.column("scored").to_numpy(zero_copy_only=False).astype(bool)
            e = acc[key]
            e["runs"] += 1
            e["scored_rows"] += int(sc.sum())
            e["insufficient_or_active_rows"] += int((~sc).sum())
            for i in by_run[rid]:
                o = us(i["onset"])
                e["incidents"][i["group"]] += 1
                fam = i["family"]
                if i["group"] == "PRECURSOR":
                    e["precursor_by_family"][fam] += 1
                    elig = sc & (t >= o - H_S * US) & (t <= o - L_MIN_S * US)
                    n = int(elig.sum())
                    e["precursor_eligible_points"].append(n)
                    if n == 0:
                        e["precursor_without_eligible_scored_decision"][fam] += 1
                elif i["group"] == "ABRUPT":
                    e["abrupt_by_family"][fam] += 1
                else:
                    e["silent_by_family"][fam] += 1
            assert (end - start) // US == 14 * 86400
    out = {"dataset_id": a.dataset, "horizon_s": H_S, "min_useful_lead_s": L_MIN_S, "decision_interval_s": DELTA_S, "splits": {}}
    for key, e in acc.items():
        inc = e["incidents"]
        total = sum(inc.values())
        pts = np.array(e["precursor_eligible_points"] or [0])
        out["splits"][key] = {
            "runs": e["runs"], "run_ids_sha256": hashlib.sha256(",".join(members[key]).encode()).hexdigest(),
            "evaluable_decision_points": e["scored_rows"], "evaluable_host_days": round(e["scored_rows"] * DELTA_S / 86400, 3),
            "calendar_host_days": e["runs"] * 14, "exposure_fraction": round(e["scored_rows"] / (e["runs"] * 20160), 5),
            "incidents_total": total, "PRECURSOR": inc["PRECURSOR"], "ABRUPT": inc["ABRUPT"], "SILENT": inc["SILENT"],
            "unpredictable_by_design": inc["ABRUPT"] + inc["SILENT"],
            "unpredictable_share": round((inc["ABRUPT"] + inc["SILENT"]) / total, 4) if total else None,
            "ceiling_share_of_all_incidents_predictable": round(inc["PRECURSOR"] / total, 4) if total else None,
            "precursor_by_family": {fam: e["precursor_by_family"].get(fam, 0) for fam in FAMILIES},
            "abrupt_by_family": dict(e["abrupt_by_family"]), "silent_by_family": dict(e["silent_by_family"]),
            "precursor_without_eligible_scored_decision": sum(e["precursor_without_eligible_scored_decision"].values()),
            "precursor_without_eligible_scored_decision_by_family": dict(e["precursor_without_eligible_scored_decision"]),
            "eligible_decision_points_per_precursor_min_median_max": [int(pts.min()), int(np.median(pts)), int(pts.max())],
        }
    out["membership_sha256_of_all_run_ids_by_split"] = {s: out["splits"][s]["run_ids_sha256"] for s in SPLITS}
    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    with open(a.out, "w", encoding="utf-8") as fh:
        json.dump(out, fh, indent=1)
    print("wrote", a.out)


if __name__ == "__main__":
    main()
