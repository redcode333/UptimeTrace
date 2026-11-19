"""Read-only, reproducible audit of the Server Machine Dataset (SMD) and of its normalised parquet copy.

    python -I scripts/audit_smd.py [--root <data-root>] --out docs/evidence/smd_audit.json
--root defaults to $UPTIMETRACE_DATA_ROOT. Reads raw files and normalised parquet; writes ONLY the JSON report.
It deliberately does NOT import ml.external: raw files are parsed here independently, so the adapter is tested, not trusted.
Only facts derivable from the files are reported; anything the files cannot tell (units, sampling interval) is marked inferred/null.
"""
from __future__ import annotations

import argparse
import glob
import hashlib
import json
import os
import re

import numpy as np
import pyarrow as pa
import pyarrow.csv as pacsv
import pyarrow.parquet as pq


def sha(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for c in iter(lambda: f.read(1 << 20), b""):
            h.update(c)
    return h.hexdigest()


def segments(y: np.ndarray) -> list[tuple[int, int]]:
    d = np.diff(np.concatenate([[0], y.astype(np.int8), [0]]))
    return list(zip(np.where(d == 1)[0].tolist(), np.where(d == -1)[0].tolist()))  # [start, end) 0-based


def parse_matrix(path: str) -> np.ndarray:
    """Strict float64 parse. Ragged rows / non-numeric tokens raise instead of being coerced."""
    ro = pacsv.ReadOptions(autogenerate_column_names=True)
    t = pacsv.read_csv(path, read_options=ro, convert_options=pacsv.ConvertOptions(column_types=None))
    cols = [t[n].to_numpy(zero_copy_only=False).astype(np.float64) for n in t.column_names]
    return np.column_stack(cols)


def raw_text_checks(path: str) -> dict:
    with open(path, "rb") as fh:
        b = fh.read()
    lines = b.split(b"\n")
    if lines and lines[-1] == b"":
        lines = lines[:-1]
    commas = np.fromiter((ln.count(b",") for ln in lines), dtype=np.int32, count=len(lines))
    first = lines[0].decode("ascii", "replace").split(",")
    dec = max((len(t.split(".")[1]) if "." in t else 0) for t in first)
    return {
        "bytes": len(b), "lines": len(lines), "crlf": b"\r\n" in b, "bom": b[:3] == b"\xef\xbb\xbf",
        "blank_lines": int(sum(1 for ln in lines if not ln.strip())), "fields_min": int(commas.min()) + 1, "fields_max": int(commas.max()) + 1,
        "ends_with_newline": b.endswith(b"\n"), "max_decimals_in_first_row": dec,
        "has_letters_other_than_e": bool(re.search(rb"[A-DF-Za-df-z]", b)), "has_exponent_notation": bool(re.search(rb"[0-9][eE][-+]?[0-9]", b)),
    }


def parse_interp(path: str) -> list[tuple[int, int, list[int]]]:
    out = []
    with open(path, encoding="utf-8") as fh:
        text = fh.read()
    for ln in text.splitlines():
        if ":" not in ln:
            continue
        rng, dims = ln.split(":", 1)
        a, b = rng.split("-")
        out.append((int(a), int(b), [int(x) for x in dims.split(",") if x.strip()]))
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=os.environ.get("UPTIMETRACE_DATA_ROOT"))
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    if not a.root:
        ap.error("--root not given and UPTIMETRACE_DATA_ROOT is not set")
    base = glob.glob(os.path.join(a.root, "smd", "raw", "src", "*", "ServerMachineDataset"))
    assert len(base) == 1, base
    base = base[0]
    norm = os.path.join(a.root, "smd", "normalised", "v1.0")
    with open(os.path.join(norm, "manifest.json"), encoding="utf-8") as fh:
        manifest = json.load(fh)
    mfiles = {e["file"]: e for e in manifest["entities"]}
    machines = sorted(os.path.basename(p)[:-4] for p in glob.glob(os.path.join(base, "train", "*.txt")))
    groups: dict[str, int] = {}
    for m in machines:
        groups[m.split("-")[1]] = groups.get(m.split("-")[1], 0) + 1

    structure = {"machines": len(machines), "machines_per_group": groups,
                 "files_per_subdir": {s: len(glob.glob(os.path.join(base, s, "*.txt"))) for s in ("train", "test", "test_label", "interpretation_label")},
                 "all_four_files_for_every_machine": all(os.path.exists(os.path.join(base, s, m + ".txt")) for m in machines for s in ("train", "test", "test_label", "interpretation_label")),
                 "files_in_dataset_dir_besides_subdirs": sorted(os.listdir(base))}
    per, seg_lens_all, raw_hash_ok, text_issues = [], [], True, []
    D = 38
    # per-dimension accumulators over train and test separately (values as float64 from raw)
    acc = {k: np.zeros(D) for k in ("n_tr", "s_tr", "ss_tr", "n_te", "s_te", "ss_te", "zero_tr", "one_tr", "const_machines", "lowcard_machines", "mono_machines", "tr_union_01", "tr_alone_01")}
    mn_tr, mx_tr = np.full(D, np.inf), np.full(D, -np.inf)
    mn_te, mx_te = np.full(D, np.inf), np.full(D, -np.inf)
    shift_over1 = nonconst_new = total_dim_pairs = 0
    dup_train_rows = overlap_rows = identical_consec_total = rows_total = 0
    boundary_ratios, first_half_frac, edge_touch, adjacent_gaps = [], [], 0, []
    faithful = {"files_checked": 0, "value_mismatch_files": 0, "max_abs_err_float32_cast": 0.0, "max_rel_err": 0.0, "label_mismatch_files": 0,
                "t_index_bad_files": 0, "schema_bad_files": 0, "row_count_bad_files": 0, "manifest_hash_bad_files": 0, "manifest_stats_bad": 0}
    interp_match = interp_total = interp_mismatch_machines = interp_same_start = interp_other = interp_exceeds = 0
    interp_dim_counts = np.zeros(D, dtype=np.int64)
    interp_dims_per_seg = []

    for m in machines:
        def p(s: str, m: str = m) -> str:
            return os.path.join(base, s, m + ".txt")

        for s in ("train", "test", "test_label"):
            tc = raw_text_checks(p(s))
            if tc["crlf"] or tc["bom"] or tc["blank_lines"] or tc["fields_min"] != tc["fields_max"] or tc["has_letters_other_than_e"]:
                text_issues.append({"file": f"{s}/{m}", **tc})
        tr, te = parse_matrix(p("train")), parse_matrix(p("test"))
        lb = parse_matrix(p("test_label"))[:, 0]
        assert tr.shape[1] == te.shape[1] == D and len(lb) == len(te)
        assert set(np.unique(lb)) <= {0.0, 1.0}
        y = lb.astype(np.int8)
        nan_tr, nan_te = int(np.isnan(tr).sum()), int(np.isnan(te).sum())
        inf_cells = int(np.isinf(tr).sum() + np.isinf(te).sum())
        segs = segments(y)
        lens = [e - s for s, e in segs]
        seg_lens_all += lens
        edge_touch += sum(1 for s, e in segs if s == 0 or e == len(y))
        adjacent_gaps += [segs[i + 1][0] - segs[i][1] for i in range(len(segs) - 1)]
        first_half_frac.append(float(y[: len(y) // 2].sum() / max(1, y.sum())))
        # raw-hash check against manifest
        for s in ("train", "test", "test_label", "interpretation_label"):
            if sha(p(s)) != manifest["raw_file_sha256"][f"{s}/{m}.txt"]:
                raw_hash_ok = False
        # interpretation_label vs point labels (file uses 1-based inclusive ranges: verified below, not assumed)
        il = parse_interp(p("interpretation_label"))
        interp_total += len(il)
        # Convention found empirically (first segments of machine-1-1): "a-b" = [a, b) 0-based, same as the point-label segments.
        ivs = sorted((a1, b1) for a1, b1, _ in il)
        seg_set = set(segs)
        interp_match += sum(1 for iv in ivs if iv in seg_set)
        interp_same_start += sum(1 for iv in ivs if iv not in seg_set and any(iv[0] == s for s, _ in segs))
        interp_other += sum(1 for iv in ivs if iv not in seg_set and not any(iv[0] == s for s, _ in segs))
        if len(ivs) != len(segs):
            interp_mismatch_machines += 1
        # does the interpretation interval ever extend outside its point-label segment?
        for iv in ivs:
            for s, e in segs:
                if s == iv[0] and iv[1] > e:
                    interp_exceeds += 1
        for _, _, dims in il:
            interp_dims_per_seg.append(len(dims))
            for dd in dims:
                interp_dim_counts[dd - 1] += 1
        # leakage / shape checks
        sets = {r.tobytes() for r in tr.astype(np.float32)}
        dup_train_rows += len(tr) - len(sets)
        overlap_rows += sum(1 for r in te.astype(np.float32) if r.tobytes() in sets)
        identical_consec_total += int((np.all(tr[1:] == tr[:-1], axis=1)).sum() + (np.all(te[1:] == te[:-1], axis=1)).sum())
        rows_total += len(tr) + len(te)
        step = np.median(np.abs(np.diff(tr, axis=0)).max(axis=1))
        boundary_ratios.append(float(np.abs(te[0] - tr[-1]).max() / step) if step > 0 else None)
        sd_tr = tr.std(axis=0)
        for j in range(D):
            total_dim_pairs += 1
            if sd_tr[j] > 0:
                if abs(te[:, j].mean() - tr[:, j].mean()) / sd_tr[j] > 1:
                    shift_over1 += 1
            elif te[:, j].std() > 0:
                nonconst_new += 1
        acc["n_tr"] += len(tr); acc["s_tr"] += tr.sum(0); acc["ss_tr"] += (tr ** 2).sum(0)
        acc["n_te"] += len(te); acc["s_te"] += te.sum(0); acc["ss_te"] += (te ** 2).sum(0)
        acc["zero_tr"] += (tr == 0).sum(0); acc["one_tr"] += (tr == 1).sum(0)
        acc["const_machines"] += (tr.std(0) == 0)
        acc["lowcard_machines"] += np.array([len(np.unique(tr[:, j])) <= 10 for j in range(D)])
        acc["mono_machines"] += (np.diff(tr, axis=0) >= 0).all(0)
        acc["tr_alone_01"] += ((tr.min(0) == 0) & (tr.max(0) == 1))
        acc["tr_union_01"] += ((np.minimum(tr.min(0), te.min(0)) == 0) & (np.maximum(tr.max(0), te.max(0)) == 1))
        mn_tr, mx_tr = np.minimum(mn_tr, tr.min(0)), np.maximum(mx_tr, tr.max(0))
        mn_te, mx_te = np.minimum(mn_te, te.min(0)), np.maximum(mx_te, te.max(0))
        # --- normalised-vs-raw faithfulness (row-by-row, exact) ---
        for split, raw, lab in (("train", tr, None), ("test", te, y)):
            fn = f"{m}.{split}.parquet"
            faithful["files_checked"] += 1
            t = pq.read_table(os.path.join(norm, fn))
            exp_cols = ["t_index"] + [f"f{j:02d}" for j in range(D)] + (["label"] if lab is not None else [])
            if (t.column_names != exp_cols or not pa.types.is_float32(t.schema.field("f00").type) or not pa.types.is_int64(t.schema.field("t_index").type)
                    or (lab is not None and not pa.types.is_int8(t.schema.field("label").type))):
                faithful["schema_bad_files"] += 1
            if t.num_rows != len(raw):
                faithful["row_count_bad_files"] += 1
                continue
            vals = np.column_stack([t[f"f{j:02d}"].to_numpy(zero_copy_only=False) for j in range(D)])
            if not np.array_equal(vals, raw.astype(np.float32), equal_nan=True):
                faithful["value_mismatch_files"] += 1
            err = np.abs(vals.astype(np.float64) - raw)
            faithful["max_abs_err_float32_cast"] = max(faithful["max_abs_err_float32_cast"], float(np.nanmax(err)))
            nz = raw != 0
            faithful["max_rel_err"] = max(faithful["max_rel_err"], float(np.nanmax(err[nz] / np.abs(raw[nz]))))
            if not np.array_equal(t["t_index"].to_numpy(), np.arange(len(raw))):
                faithful["t_index_bad_files"] += 1
            if lab is not None and not np.array_equal(t["label"].to_numpy(), lab):
                faithful["label_mismatch_files"] += 1
            if sha(os.path.join(norm, fn)) != mfiles[fn]["sha256"]:
                faithful["manifest_hash_bad_files"] += 1
            e = mfiles[fn]
            if e["rows"] != len(raw) or e["nan_cells"] != int(np.isnan(raw).sum()) or (lab is not None and (e["anomaly_points"] != int(lab.sum()) or e["segments"] != len(segs))):
                faithful["manifest_stats_bad"] += 1
        per.append({"machine": m, "train_rows": len(tr), "test_rows": len(te), "total_rows": len(tr) + len(te), "anomaly_points": int(y.sum()),
                    "anomaly_ratio": round(float(y.mean()), 5), "segments": len(segs), "seg_len_min_median_max": [min(lens), int(np.median(lens)), max(lens)] if lens else None,
                    "nan_train": nan_tr, "nan_test": nan_te, "inf_cells": inf_cells, "train_equals_test_rows": len(tr) == len(te)})

    n_tr, n_te = acc["n_tr"], acc["n_te"]
    mu_tr, mu_te = acc["s_tr"] / n_tr, acc["s_te"] / n_te
    sd_tr = np.sqrt(np.maximum(acc["ss_tr"] / n_tr - mu_tr ** 2, 0))
    feats = [{"dim": j, "name_in_normalised": f"f{j:02d}", "train_min": round(float(mn_tr[j]), 6), "train_max": round(float(mx_tr[j]), 6),
              "test_min": round(float(mn_te[j]), 6), "test_max": round(float(mx_te[j]), 6), "pooled_train_mean": round(float(mu_tr[j]), 5),
              "pooled_train_std": round(float(sd_tr[j]), 5), "pooled_test_mean": round(float(mu_te[j]), 5),
              "frac_train_exact_zero": round(float(acc["zero_tr"][j] / n_tr[j]), 4), "frac_train_exact_one": round(float(acc["one_tr"][j] / n_tr[j]), 5),
              "machines_constant_in_train": int(acc["const_machines"][j]), "machines_le10_distinct_values": int(acc["lowcard_machines"][j]),
              "machines_monotone_nondecreasing_train": int(acc["mono_machines"][j]),
              "machines_train_alone_spans_0_1": int(acc["tr_alone_01"][j]), "machines_train_plus_test_span_0_1": int(acc["tr_union_01"][j]),
              "times_in_interpretation_labels": int(interp_dim_counts[j])} for j in range(D)]
    tot_tr, tot_te = sum(r["train_rows"] for r in per), sum(r["test_rows"] for r in per)
    tot_an = sum(r["anomaly_points"] for r in per)
    L = np.array(seg_lens_all)
    report = {
        "structure": structure, "text_level_issues": text_issues, "raw_hashes_match_manifest": raw_hash_ok,
        "totals": {"train_rows": tot_tr, "test_rows": tot_te, "rows": tot_tr + tot_te, "anomaly_points_test": tot_an,
                   "anomaly_ratio_test": round(tot_an / tot_te, 5), "segments": len(L),
                   "nan_cells": int(sum(r["nan_train"] + r["nan_test"] for r in per)), "inf_cells": int(sum(r["inf_cells"] for r in per)),
                   "machines_with_train_len_eq_test_len": int(sum(r["train_equals_test_rows"] for r in per)),
                   "rows_per_machine_min_median_max": [int(min(r["total_rows"] for r in per)), int(np.median([r["total_rows"] for r in per])), max(r["total_rows"] for r in per)]},
        "sampling": {"timestamps_in_files": False, "interval_in_files": None,
                     "inferred_minutes_if_5_weeks": "5 weeks = 50,400 min; median rows per machine reported above. This is arithmetic from a README claim, NOT a file fact.",
                     "five_weeks_in_minutes": 5 * 7 * 1440},
        "segments": {"count": len(L), "len_min": int(L.min()), "len_p25": int(np.percentile(L, 25)), "len_median": int(np.median(L)), "len_p75": int(np.percentile(L, 75)),
                     "len_p90": int(np.percentile(L, 90)), "len_max": int(L.max()), "len_mean": round(float(L.mean()), 2), "len_le_5": int((L <= 5).sum()),
                     "len_ge_100": int((L >= 100).sum()), "segments_touching_test_start_or_end": int(edge_touch),
                     "gap_between_consecutive_segments_min_median": [int(min(adjacent_gaps)), int(np.median(adjacent_gaps))] if adjacent_gaps else None,
                     "gaps_le_10_rows": int(sum(1 for g in adjacent_gaps if g <= 10)),
                     "share_of_anomaly_points_in_first_half_of_test_mean_over_machines": round(float(np.mean(first_half_frac)), 3),
                     "share_of_anomaly_points_in_longest_10pct_segments": round(float(np.sort(L)[::-1][: max(1, len(L) // 10)].sum() / L.sum()), 3)},
        "interpretation_label": {"segments_listed": interp_total, "range_convention": "a-b = [a,b) 0-based (empirical)", "intervals_identical_to_a_point_label_segment": interp_match,
                                 "intervals_same_start_different_end": interp_same_start, "intervals_with_start_not_matching_any_segment": interp_other,
                                 "intervals_whose_end_exceeds_the_point_label_segment": interp_exceeds,
                                 "machines_where_interval_count_differs_from_segment_count": interp_mismatch_machines, "dims_per_segment_min_median_max": [int(min(interp_dims_per_seg)), int(np.median(interp_dims_per_seg)), int(max(interp_dims_per_seg))]},
        "leakage": {"exact_duplicate_train_rows": int(dup_train_rows), "test_rows_identical_to_a_train_row_same_machine": int(overlap_rows),
                    "consecutive_identical_rows_share": round(identical_consec_total / rows_total, 4),
                    "train_end_to_test_start_jump_vs_median_step_ratio_median": round(float(np.median([r for r in boundary_ratios if r is not None])), 2),
                    "machine_dim_pairs": total_dim_pairs, "pairs_test_mean_shift_gt_1_train_std": int(shift_over1),
                    "pairs_constant_in_train_but_varying_in_test": int(nonconst_new),
                    "global_value_range_train_min_max": [float(mn_tr.min()), float(mx_tr.max())], "global_value_range_test_min_max": [float(mn_te.min()), float(mx_te.max())]},
        "faithfulness_of_normalised_v1_0": {**faithful},
        "per_machine": per, "features": feats,
        "label_semantics": "label=1: expert-labelled anomalous test point. interpretation_label lists contributing dimensions per segment. Neither is an outage or carries impact time.",
    }
    os.makedirs(os.path.dirname(os.path.abspath(a.out)), exist_ok=True)
    with open(a.out, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=1, default=str)
    brief = {k: v for k, v in report.items() if k not in ("per_machine", "features")}
    print(json.dumps(brief, indent=1, default=str)[:6000])


if __name__ == "__main__":
    main()
