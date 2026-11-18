"""M3.3 focused tests: no future data, lookback boundaries, counter resets, gaps/missingness, label alignment, split isolation."""
from __future__ import annotations

import builtins
import glob
import json
import shutil
from pathlib import Path

import numpy as np
import pytest

from ml import build_features as bf
from ml import build_labels as bl
from ml import features as ft

IDX = ft.INDEX
ART = Path(__file__).resolve().parents[3] / "artifacts"


def make(times, gen=lambda tau: 50.0 + 0.0 * tau, lag=1.0, rate=1e6, **over):
    """Six series sharing observation times. Percent series use gen(tau); counters grow by `rate` bytes/s."""
    tau = np.array(sorted(times), dtype=float)
    env = tau + lag
    s = {}
    for name in ft.PCT:
        s[name] = (tau, env.copy(), np.asarray(gen(tau), dtype=float))
    for name in ft.NET:
        s[name] = (tau, env.copy(), 1e9 + rate * tau)
    s.update(over)
    return s


def feat(series, t):
    return ft.compute_features(series, np.array([float(t)]))[0]


def get(row, name):
    return float(row[IDX[name]])


GRID = list(range(0, 12001, 60))


# ------------------------------------------------------------------------------------------------ no future data
def test_changing_everything_after_t_does_not_change_features():
    t = 6005.0
    base = make(GRID, gen=lambda x: 40 + 10 * np.sin(x / 500.0))
    ref = feat(base, t)
    poisoned = {}
    for name, (tau, env, val) in base.items():
        v = val.copy()
        future = (tau > t) | (env > t)
        v[future] = v[future] * 1000 + 12345                    # garbage in everything not yet available
        poisoned[name] = (tau, env, v)
    assert np.array_equal(ref, feat(poisoned, t), equal_nan=True)
    truncated = {n: (tau[env <= t], env[env <= t], val[env <= t]) for n, (tau, env, val) in base.items()}
    assert np.array_equal(ref, feat(truncated, t), equal_nan=True)         # table cut at env <= t gives identical features


def test_buffered_observations_do_not_count_until_their_replay_arrives():
    t_during, t_after = 9000.0, 9810.0
    a, b, replay = 8000, 9800, 9803.0
    tau = np.array(GRID, dtype=float)
    env = tau + 1.0
    env[(tau >= a) & (tau < b)] = replay
    s = {n: (tau, env, np.where((tau >= a) & (tau < b), 99.0, 50.0)) for n in ft.PCT}
    s.update({n: (tau, env, 1e9 + 1e6 * tau) for n in ft.NET})
    during = feat(s, t_during)
    assert np.isnan(get(during, "cpu_max_900")) and get(during, "cpu_n_900") == 0                          # nothing available in the last 15 min
    assert get(during, "cpu_max_3600") == 50.0 and get(during, "cpu_age_s") == pytest.approx(9000 - 7980)   # the 99.0 backfill is invisible
    assert np.isnan(get(during, "cpu_last"))                                                                # stale: no forward fill
    after = feat(s, t_after)
    assert get(after, "cpu_max_900") == 99.0                                                                # visible once replayed


# ------------------------------------------------------------------------------------------------ lookback boundaries
def test_window_is_open_at_t_minus_w_and_closed_at_t():
    t = 6005.0
    times = [x for x in GRID if x not in (5700, 5760)] + [5705.0, 5705.5, 6005.0]       # specials around t - 300 = 5705
    tau = np.array(sorted(set(times)), dtype=float)
    val = np.full(tau.size, 10.0)
    val[tau == 5705.0] = 1000.0          # exactly t - 300: outside the 300 s window, inside the 900 s window
    val[tau == 5705.5] = 500.0           # just inside
    s = make(times)
    for n in ft.PCT:
        s[n] = (tau, tau + 1.0, val.copy())
    r = feat(s, t)
    assert get(r, "cpu_max_300") == 500.0 and get(r, "cpu_max_900") == 1000.0
    # the observation at exactly t is used iff its envelope arrived by t
    for env_at_t, expect_last in ((6005.0, 77.0), (6005.5, 10.0)):
        tau2 = tau.copy(); env2 = tau2 + 1.0
        v2 = val.copy(); v2[tau2 == 6005.0] = 77.0
        env2[tau2 == 6005.0] = env_at_t
        s2 = make(times)
        for n in ft.PCT:
            s2[n] = (tau2, env2, v2.copy())
        assert get(feat(s2, t), "cpu_last") == expect_last


def test_statistics_match_numpy_on_known_data():
    t = 6005.0
    s = make(GRID, gen=lambda x: 20 + 0.01 * x)                   # 0.6 per minute
    r = feat(s, t)
    tau = np.array([x for x in GRID if 6005 - 900 < x <= 6005 and x + 1 <= t], dtype=float)
    v = 20 + 0.01 * tau
    assert get(r, "cpu_mean_900") == pytest.approx(v.mean(), rel=1e-5) and get(r, "cpu_std_900") == pytest.approx(v.std(ddof=1), rel=1e-4)
    assert get(r, "cpu_slope_900") == pytest.approx(0.6, rel=1e-4) and get(r, "cpu_delta_900") == pytest.approx(v[-1] - v[0], rel=1e-5)
    assert get(r, "cpu_max_900") == pytest.approx(v.max(), rel=1e-5) and get(r, "cpu_min_900") == pytest.approx(v.min(), rel=1e-5)
    assert get(r, "cpu_last") == pytest.approx(v[-1], rel=1e-5) and get(r, "cpu_n_900") == len(v)
    assert get(r, "cpu_cov_300") == 1.0 and get(r, "cpu_missing_300") == 0.0 and get(r, "cpu_cadence_s") == 60.0


def test_disk_available_capacity_is_derived_from_utilisation():
    t = 6005.0
    s = make(GRID, gen=lambda x: 60 + 0.002 * x)
    r = feat(s, t)
    assert get(r, "disk_root_free_last") == pytest.approx(100 - get(r, "disk_root_last"), abs=1e-4)
    assert get(r, "disk_data_free_min_900") == pytest.approx(100 - get(r, "disk_data_max_900"), abs=1e-4)


# ------------------------------------------------------------------------------------------------ counters
def test_counter_reset_is_not_turned_into_a_rate_and_is_counted():
    t = 6005.0
    tau = np.array(GRID, dtype=float)
    c = 5e12 + 1e6 * tau
    c[tau >= 5700] = 200 + 1e6 * (tau[tau >= 5700] - 5700)       # reboot: counter restarts near zero at 5700
    s = make(GRID, **{"rx": (tau, tau + 1.0, c)})
    r = feat(s, t)
    assert get(r, "rx_resets_900") == 1 and get(r, "rx_resets_3600") == 1
    assert get(r, "rx_resets_300") == 0                      # the decreasing pair (5640 -> 5700) lies before the 300 s window (5705, 6005]
    assert get(r, "rx_rate_max_900") == pytest.approx(1e6, rel=1e-6) and get(r, "rx_rate_min_900") == pytest.approx(1e6, rel=1e-6)
    assert get(r, "rx_rate_mean_900") == pytest.approx(1e6, rel=1e-6)          # no negative rate, no 5e18 B/s spike across the reset
    assert get(r, "rx_rate_last") == pytest.approx(1e6, rel=1e-6)
    pairs_clean = get(feat(make(GRID), t), "rx_pairs_900")
    assert get(r, "rx_pairs_900") == pairs_clean - 1                           # exactly the pair across the reset is dropped
    assert get(feat(make(GRID), t), "rx_resets_900") == 0


def test_rates_are_not_computed_across_a_telemetry_gap():
    t = 6005.0
    times = [x for x in GRID if not 5100 < x < 5460]            # 360 s hole: that interval exceeds 3 x cadence
    r = feat(make(times, rate=2e6), t)
    clean = feat(make(GRID, rate=2e6), t)
    assert (get(clean, "rx_pairs_900"), get(r, "rx_pairs_900")) == (14, 9)      # 15 vs 10 observations in (5105, 6005]; no pair spans the hole
    assert get(r, "rx_rate_max_900") == pytest.approx(2e6, rel=1e-6)           # the gap interval is dropped, not averaged in
    assert get(r, "rx_cov_900") < get(clean, "rx_cov_900")


# ------------------------------------------------------------------------------------------------ missing data
def test_gap_inside_a_window_gives_nan_statistics_and_indicators():
    t = 8005.0
    times = [x for x in GRID if not 7380 < x < 7980]            # nothing for 10 minutes; one observation (7980) is back before t
    r = feat(make(times), t)
    assert get(r, "cpu_n_300") == 1 and get(r, "cpu_missing_300") == 1.0 and np.isnan(get(r, "cpu_mean_300"))
    assert np.isnan(get(r, "cpu_std_300")) and np.isnan(get(r, "cpu_slope_300")) and np.isnan(get(r, "cpu_delta_300"))
    assert get(r, "cpu_cov_300") < 0.5 and get(r, "cpu_cov_3600") >= 0.5
    assert get(r, "rx_missing_300") == 1.0 and np.isnan(get(r, "rx_rate_mean_300"))
    assert not np.isnan(get(r, "cpu_mean_3600"))                # the long window still has data


def test_stale_series_has_no_forward_filled_last_value_and_is_counted():
    t = 8005.0
    s = make([x for x in GRID if x < 7000])
    r = feat(s, t)
    assert np.isnan(get(r, "cpu_last")) and get(r, "cpu_age_s") > 900 and get(r, "n_series_stale") == 6
    assert np.isnan(get(r, "disk_root_free_last")) and np.isnan(get(r, "rx_rate_last"))


def test_isolated_omission_does_not_create_missing_values():
    t = 6005.0
    r = feat(make([x for x in GRID if x != 5880]), t)
    assert get(r, "cpu_missing_300") == 0.0 and get(r, "cpu_cov_300") == 1.0 and not np.isnan(get(r, "cpu_mean_300"))
    assert get(r, "n_series_stale") == 0


def test_absent_series_is_all_nan_with_zero_coverage():
    t = 6005.0
    s = make(GRID, **{"tx": (np.zeros(0), np.zeros(0), np.zeros(0))})
    r = feat(s, t)
    assert get(r, "tx_missing_900") == 1.0 and get(r, "tx_cov_900") == 0.0 and np.isnan(get(r, "tx_rate_mean_900")) and get(r, "n_series_stale") == 1


# ------------------------------------------------------------------------------------------------ manifest
def test_registry_is_consistent_and_contains_no_target_like_names():
    assert len(ft.NAMES) == len(set(ft.NAMES)) == ft.F == 196
    assert all(m["dtype"] == "float32" and m["source"] and "unit" in m for m in ft.manifest_entries())
    banned = ("target", "label", "incident", "fault", "onset", "precursor", "group", "exclu", "scored")
    assert not [n for n in ft.NAMES if any(b in n for b in banned)]
    assert {m["window_s"] for m in ft.manifest_entries()} == {None, 300, 900, 3600}


# ------------------------------------------------------------------------------------------------ real subset: alignment and isolation
def _dirs():
    f = sorted(glob.glob(str(ART / "features" / "uptimetrace-m31-seed0-*")))
    l = sorted(glob.glob(str(ART / "labels" / "uptimetrace-m31-seed0-*")))
    d = sorted(glob.glob(str(ART / "datasets" / "uptimetrace-m31-seed0-*")))
    if not (f and l and d):
        pytest.skip("seed-0 dataset, labels and features not present")
    return Path(d[0]), Path(l[0]), Path(f[0])


def test_features_and_labels_align_row_for_row_for_every_horizon():
    ds, lab, feat_dir = _dirs()
    m = json.load(open(feat_dir / "features_manifest.json"))
    assert m["n_features"] == 196 and m["feature_version"] == ft.VERSION and m["lookback_windows_s"] == [300, 900, 3600]
    for H in (900, 1800, 3600):
        xy = bf.load_xy(feat_dir, lab, ["train", "val", "test_iid", "test_ood"], H)
        n = len(xy["y"])
        assert xy["X"].shape == (n, 196) and n == m["n_rows"] == 137855
        labs = bl.load_labels(lab, horizons=[H])
        sc = np.array(labs.column("scored").to_pylist(), dtype=bool)
        assert sc.sum() == n
        assert (xy["y"] == bl.targets_numpy(labs)[sc]).all()                       # same targets, same order, nothing reordered
        assert set(xy["y"].tolist()) == {0, 1} and set(xy["label_class"]) <= {"positive", "negative", "unscorable"}
    assert xy["feature_names"] == ft.NAMES and not any(k in xy["feature_names"] for k in ("target", "y", "scored"))
    assert np.isnan(xy["X"]).mean() < 0.1


def test_split_ids_and_row_identity_are_consistent():
    ds, lab, feat_dir = _dirs()
    runs = {json.loads(l)["run_id"]: json.loads(l) for l in open(ds / "runs.jsonl")}
    xy = bf.load_xy(feat_dir, lab, ["train", "val", "test_iid", "test_ood"], 1800)
    ids = xy["ids"]
    assert all(runs[r]["split"] == s for r, s in zip(ids["run_id"][::503], ids["split"][::503]))
    assert all(runs[r]["host_id"] == h for r, h in zip(ids["run_id"][::503], ids["host_id"][::503]))
    start = {r: bl.lb.to_us(runs[r]["start_ts"]) for r in set(ids["run_id"])}
    off = np.array([ts - start[r] for r, ts in zip(ids["run_id"], ids["decision_ts"])])
    assert (off == 5_000_000 + 60_000_000 * ids["decision_index"].astype(np.int64)).all()      # t_j = run_start + 5 s + 60 s * j


def test_split_isolation_features_for_train_never_open_other_splits(tmp_path):
    ds, lab, feat_dir = _dirs()
    poisoned = tmp_path / "f"
    shutil.copytree(feat_dir, poisoned)
    for sp in ("val", "test_iid", "test_ood"):
        for f in (poisoned / "features" / f"split={sp}").rglob("*.parquet"):
            f.write_bytes(b"POISON")
    xy = bf.load_xy(poisoned, lab, ["train"], 1800)
    assert set(xy["ids"]["split"]) == {"train"} and xy["X"].shape[0] == 39389
    with pytest.raises(Exception):
        bf.load_xy(poisoned, lab, ["val"], 1800)


def test_feature_pipeline_reads_no_targets_and_no_incident_or_fault_sidecars(tmp_path, monkeypatch):
    ds, lab, feat_dir = _dirs()
    run = next(json.loads(l) for l in open(ds / "runs.jsonl") if '"r0095"' in l)
    opened, read_cols = [], []
    real_open, real_pf = builtins.open, bf.pq.ParquetFile

    def spy_open(f, *a, **k):
        opened.append(str(f)); return real_open(f, *a, **k)

    class SpyPF(real_pf):
        def read(self, columns=None, *a, **k):
            read_cols.append(columns); return super().read(columns, *a, **k)
    monkeypatch.setattr(builtins, "open", spy_open)
    monkeypatch.setattr(bf.pq, "ParquetFile", SpyPF)
    bf.features_for_run((str(ds), str(lab), str(tmp_path), run))
    assert not [p for p in opened if p.endswith(("incidents.jsonl", "faults.jsonl", "benign_episodes.jsonl", "gaps.jsonl"))]
    cols = {c for cs in read_cols if cs for c in cs}
    assert cols == {"run_id", "host_id", "decision_index", "decision_ts", "scored"}      # labels: identifiers and scored flag only
    out = bf.pq.read_table(tmp_path / "features" / "split=val" / "run=r0095.parquet")
    ref = bf.pq.read_table(feat_dir / "features" / "split=val" / "run=r0095.parquet")
    assert out.column_names == ref.column_names
    for n in out.column_names:                                                          # rerun reproduces the stored features (NaN == NaN)
        a, b = out.column(n).combine_chunks(), ref.column(n).combine_chunks()
        if n in ft.NAMES:
            assert np.array_equal(a.to_numpy(zero_copy_only=False), b.to_numpy(zero_copy_only=False), equal_nan=True), n
        else:
            assert a.to_pylist() == b.to_pylist(), n
