"""Tests for ml.evaluate. Synthetic fixtures only: no dataset, test split or model artifact is read here."""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from ml import evaluate as ev
from ml import build_labels as bl
from ml import features as ft
from ml import labels as lb
from ml.predict import SchemaError, load_model

H = 1800
ONE = 1_000_000


# ---------------------------------------------------------------------------------------------- synthetic dataset fixture
def _write_run(path: Path, run_id: str, host: str, n: int, extra_cols=(), drop=(), seed=0):
    rng = np.random.default_rng(seed)
    cols = {"run_id": pa.array([run_id] * n), "host_id": pa.array([host] * n),
            "decision_index": pa.array(np.arange(n, dtype=np.int64)),
            "decision_ts": pa.array(np.arange(n, dtype=np.int64) * 60 * ONE)}
    for name in ft.NAMES:
        if name in drop:
            continue
        cols[name] = pa.array(rng.random(n).astype(np.float32))
    for name in extra_cols:
        cols[name] = pa.array(rng.random(n).astype(np.float32))
    path.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(pa.table(cols), path)


def make_fixture(root: Path, *, names=None, drop=(), splits=None, hash_break=None, file_extra=()):
    """A tiny dataset with the same layout as the real artifacts: runs.jsonl, features/split=*/run=*.parquet, manifests."""
    dsid = "uptimetrace-test-fixture"
    splits = splits or {"train": ["r0000", "r0001"], "val": ["r0002"], "test_iid": ["r0003"]}
    dataset, feats, labs = root / "dataset", root / "features", root / "labels"
    dataset.mkdir(parents=True), feats.mkdir(), labs.mkdir()
    (dataset / "manifest.json").write_text(json.dumps({"dataset_id": dsid}), encoding="utf-8")
    runs_lines = []
    files = {}
    for sp, ids in splits.items():
        for i, rid in enumerate(ids):
            host = f"h-{rid}"
            runs_lines.append(json.dumps({"run_id": rid, "split": sp, "host_id": host}))
            rel = f"features/split={sp}/run={rid}.parquet"
            _write_run(feats / rel, rid, host, 40, extra_cols=file_extra, drop=drop, seed=i)
            files[rel] = ev.sha256_file(feats / rel)
    (dataset / "runs.jsonl").write_text("\n".join(runs_lines) + "\n", encoding="utf-8")
    if hash_break:
        files[hash_break] = "0" * 64
    feature_list = [{"name": n} for n in (ft.NAMES if names is None else names)]
    (feats / "features_manifest.json").write_text(json.dumps({
        "source_dataset_id": dsid, "feature_version": ft.VERSION, "features": feature_list, "files_sha256": files}), encoding="utf-8")
    (labs / "labels_manifest.json").write_text(json.dumps({
        "source_dataset_id": dsid, "labeler_version": bl.LABELER_VERSION, "config": {}, "files_sha256": {}}), encoding="utf-8")
    return dataset, feats, labs


# ---------------------------------------------------------------------------------------------- input verification
def test_clean_fixture_passes_and_reports_split_definitions(tmp_path):
    d, f, l = make_fixture(tmp_path)
    p = ev.verify_inputs(d, f, l, H, splits=("train", "val"))
    assert p["split_runs"] == {"train": ["r0000", "r0001"], "val": ["r0002"]}
    assert p["file_hashes_checked"] == 3 and "quarantined_files" not in p


def test_test_split_files_are_not_opened(tmp_path, monkeypatch):
    d, f, l = make_fixture(tmp_path)
    opened = []
    real = ev.sha256_file

    def spy(path):
        opened.append(Path(path).as_posix())
        return real(path)

    monkeypatch.setattr(ev, "sha256_file", spy)
    ev.verify_inputs(d, f, l, H, splits=("train", "val"))
    assert opened, "the spy must have seen hash checks"
    assert not any("split=test_" in p for p in opened)


def test_unexpected_feature_column_is_rejected(tmp_path):
    d, f, l = make_fixture(tmp_path, file_extra=("bogus_feature",))
    with pytest.raises(ev.ProtocolError, match="bogus_feature"):
        ev.verify_inputs(d, f, l, H)


def test_missing_registry_feature_is_rejected(tmp_path):
    d, f, l = make_fixture(tmp_path, drop=("cpu_mean_300",))
    with pytest.raises(ev.ProtocolError, match="cpu_mean_300"):
        ev.verify_inputs(d, f, l, H)


def test_retired_rate_columns_are_rejected_not_ignored(tmp_path):
    retired = ("rx_rate_slope_300", "rx_rate_std_300", "tx_rate_slope_300", "tx_rate_std_300")
    d, f, l = make_fixture(tmp_path, names=list(ft.NAMES) + list(retired), file_extra=retired)
    with pytest.raises(ev.ProtocolError, match="registry"):
        ev.verify_inputs(d, f, l, H)


def test_feature_order_mismatch_in_manifest_is_rejected(tmp_path):
    names = list(ft.NAMES)
    names[0], names[1] = names[1], names[0]
    d, f, l = make_fixture(tmp_path, names=names)
    with pytest.raises(ev.ProtocolError, match="order"):
        ev.verify_inputs(d, f, l, H)


def test_hash_mismatch_is_rejected(tmp_path):
    d, f, l = make_fixture(tmp_path, hash_break="features/split=train/run=r0001.parquet")
    with pytest.raises(ev.ProtocolError, match="hash mismatch"):
        ev.verify_inputs(d, f, l, H)


def test_run_stored_under_wrong_split_is_rejected(tmp_path):
    d, f, l = make_fixture(tmp_path)
    lines = (d / "runs.jsonl").read_text().splitlines()
    lines = [json.dumps({**json.loads(x), "split": "test_iid"}) if '"r0002"' in x else x for x in lines]
    (d / "runs.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")
    with pytest.raises(ev.ProtocolError, match="r0002"):
        ev.verify_inputs(d, f, l, H)


def test_host_shared_across_splits_is_rejected(tmp_path):
    d, f, l = make_fixture(tmp_path)
    lines = [json.dumps({**json.loads(x), "host_id": "shared"}) if json.loads(x)["run_id"] in ("r0000", "r0002") else x
             for x in (d / "runs.jsonl").read_text().splitlines()]
    (d / "runs.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")
    with pytest.raises(ev.ProtocolError, match="hosts appear"):
        ev.verify_inputs(d, f, l, H)


def test_manifests_from_another_dataset_are_rejected(tmp_path):
    d, f, l = make_fixture(tmp_path)
    m = json.loads((f / "features_manifest.json").read_text())
    m["source_dataset_id"] = "some-other-dataset"
    (f / "features_manifest.json").write_text(json.dumps(m), encoding="utf-8")
    with pytest.raises(ev.ProtocolError, match="not built from"):
        ev.verify_inputs(d, f, l, H)


# ---------------------------------------------------------------------------------------------- selection and preprocessing
def test_training_selector_drops_unscorable_and_thins_negatives():
    sel = ev.train_select(stride=5)
    lc = np.array(["positive", "negative", "negative", "unscorable", "negative", "positive"])
    di = np.array([0, 2, 3, 2, 4, 4])
    keep = sel(lc, di)
    assert keep.tolist() == [True, True, False, False, False, True]   # unscorable never kept; negatives thinned; positives kept


def test_test_split_status_is_blocked_without_freeze_record(tmp_path):
    msg = ev.test_status(tmp_path)
    assert msg.startswith("not evaluated") and "frozen_run.json" in msg


def test_preprocessing_is_fitted_on_training_rows_only():
    rng = np.random.default_rng(1)
    X = rng.normal(size=(300, len(ev.MODEL_INPUTS)))
    X[::7, 0] = np.nan
    y = (rng.random(300) < 0.2).astype(np.int8)
    fitted = ev.fit_models(X, y, int((y == 0).sum()), int((y == 1).sum()))
    imp = fitted["logreg"]["estimator"].named_steps["simpleimputer"]
    expected = np.nanmedian(X, axis=0)
    assert np.allclose(imp.statistics_, expected)
    # a validation matrix with very different values must not change the fitted statistics
    assert not np.allclose(imp.statistics_, np.nanmedian(X + 1000, axis=0))


def test_feature_order_mismatch_when_scoring_is_rejected(tmp_path):
    rng = np.random.default_rng(2)
    X = rng.normal(size=(200, len(ev.MODEL_INPUTS)))
    y = (rng.random(200) < 0.3).astype(np.int8)
    fitted = ev.fit_models(X, y, 140, 60)
    schema = ev.model_schema("logreg", H, 0.5)
    ev.save_model(tmp_path, "logreg", schema, fitted["logreg"]["estimator"])
    model = load_model(tmp_path / "models" / "logreg")
    good = np.zeros((3, len(ft.NAMES)), dtype=np.float32)
    assert model.predict_risk(good, feature_names=ft.NAMES).shape == (3,)
    with pytest.raises(SchemaError):
        model.predict_risk(good, feature_names=ft.NAMES[:-1])


def test_saved_models_reload_and_reproduce_predictions(tmp_path):
    rng = np.random.default_rng(3)
    X = rng.normal(size=(400, len(ev.MODEL_INPUTS)))
    y = (rng.random(400) < 0.25).astype(np.int8)
    fitted = ev.fit_models(X, y, int((y == 0).sum()), int((y == 1).sum()))
    Xfull = np.zeros((400, len(ft.NAMES)), dtype=np.float32)
    idx = [ft.NAMES.index(n) for n in ev.MODEL_INPUTS]
    Xfull[:, idx] = X
    for kind in ("logreg", "xgboost"):
        ev.save_model(tmp_path, kind, ev.model_schema(kind, H, 0.5), fitted[kind]["estimator"])
        reloaded = load_model(tmp_path / "models" / kind).predict_risk(Xfull, feature_names=ft.NAMES)
        inmem = ev.RiskModel(ev.model_schema(kind, H, 0.5), fitted[kind]["estimator"]).predict_risk(Xfull, feature_names=ft.NAMES)
        assert np.array_equal(reloaded, inmem), kind


def test_fitting_is_deterministic_for_fixed_seed():
    rng = np.random.default_rng(4)
    X = rng.normal(size=(300, len(ev.MODEL_INPUTS)))
    y = (rng.random(300) < 0.3).astype(np.int8)
    a = ev.fit_models(X, y, 210, 90)["logreg"]["estimator"].predict_proba(X)
    b = ev.fit_models(X, y, 210, 90)["logreg"]["estimator"].predict_proba(X)
    assert np.array_equal(a, b)


# ---------------------------------------------------------------------------------------------- metrics
def test_classification_metrics_match_hand_computed_values():
    y = np.array([1, 1, 0, 0, 0])
    s = np.array([0.9, 0.4, 0.6, 0.1, 0.2])
    c = ev.classification(y, s, 0.5)                    # predictions 1,0,1,0,0
    assert c["confusion"] == {"tn": 2, "fp": 1, "fn": 1, "tp": 1}
    assert c["precision"] == 0.5 and c["recall"] == 0.5 and c["f1"] == 0.5
    assert c["pr_auc"] == pytest.approx(0.8333, abs=1e-4)


def test_undefined_metrics_are_none_not_zero():
    y = np.array([1, 0, 0])
    s = np.array([0.2, 0.3, 0.1])
    c = ev.classification(y, s, 0.95)                   # nothing alerts
    assert c["precision"] is None and c["f1"] is None and c["recall"] == 0.0


def test_brier_and_skill_against_constant_prevalence():
    y = np.array([1.0, 0.0])
    s = np.array([0.8, 0.3])
    cal = ev.calibration(y, s, p_train=0.5, val_scores=s)
    assert cal["brier"] == pytest.approx(0.065)
    assert cal["brier_constant_prevalence"] == pytest.approx(0.25)
    assert cal["brier_skill"] == pytest.approx(1 - 0.065 / 0.25, abs=1e-4)


def test_alert_dedup_suppresses_candidates_for_900_seconds():
    run = np.array(["r0"] * 17)
    ts = np.arange(17, dtype=np.int64) * 60 * ONE           # 0..960 s, every minute
    scores = np.ones(17)
    ev_list = ev.alert_events(run, ts, scores, 0.5, np.ones(17, dtype=int))
    assert [t for _, t, _ in ev_list] == [0, 900 * ONE]     # next alert only once 900 s have passed


def _pre(run, iid, onset_s, group="PRECURSOR", family="cpu_runaway"):
    return lb.IncidentMeta(run, iid, family, group, int(onset_s * ONE))


def test_stage1_credits_earliest_alert_then_later_alert_is_duplicate():
    metas = [_pre("r0", "i1", 1000)]
    events = [("r0", 0, "positive"), ("r0", 300 * ONE, "positive")]        # 300 s is 700 s before onset, inside the window
    cls = ev.classify_alerts(events, metas, H, {"r0"})
    assert cls["outcome"] == ["hit", "duplicate"]
    assert cls["credited"]["i1"] == (0, 0)                  # (alert index, alert decision time in us)


def test_alert_inside_the_120_second_guard_is_late_not_a_hit():
    metas = [_pre("r0", "i1", 1000)]
    cls = ev.classify_alerts([("r0", 950 * ONE, "positive")], metas, H, {"r0"})   # 50 s before onset
    assert cls["outcome"] == ["late"] and cls["credited"] == {}


def test_unscorable_alert_is_a_strict_false_alarm_not_a_correct_alert():
    metas = [_pre("r0", "s1", 5000, group="SILENT", family="silent_crash")]
    cls = ev.classify_alerts([("r0", 1000 * ONE, "unscorable")], metas, H, {"r0"})
    assert cls["outcome"] == ["coincident_unpredictable"]
    summary = ev.alert_summary(cls["outcome"], host_days=1.0)
    assert summary["strict_false_alarms"] == 1 and summary["spec_false_alarms"] == 0


def test_negative_alert_with_no_incident_is_false_alarm():
    cls = ev.classify_alerts([("r0", 1000 * ONE, "negative")], [], H, {"r0"})
    assert cls["outcome"] == ["false_alarm"]


def test_incident_detection_lead_time_and_non_primary_split():
    onset = 10_000
    metas = [_pre("r0", "i1", onset, family="cpu_runaway"),
             _pre("r0", "i2", onset + 50_000, family="disk_fill"),
             _pre("r0", "i3", onset + 80_000, group="SILENT", family="silent_crash"),
             _pre("r0", "i4", onset + 90_000, group="ABRUPT", family="net_flood")]
    events = [("r0", (onset - 600) * ONE, "positive"),          # 600 s early warning for i1
              ("r0", (onset + 80_000 - 900) * ONE, "unscorable")]  # coincident with the SILENT incident i3
    cls = ev.classify_alerts(events, metas, H, {"r0"})
    blk = ev.incident_block(metas, cls, {"r0"})
    prim = blk["primary_precursor"]
    assert prim["incidents"] == 2 and prim["detected"] == 1 and prim["recall"] == 0.5
    assert prim["lead_time"]["median_s"] == 600.0
    assert prim["per_family"]["cpu_runaway"]["detected"] == 1 and prim["per_family"]["disk_fill"]["detected"] == 0
    assert prim["statistically_unsupported"] is True            # n < 30
    sil = blk["non_primary_diagnostic"]["SILENT"]
    assert sil["detected"] == 0 and sil["coincidentally_warned_diagnostic_not_success"]["count"] == 1
    assert blk["non_primary_diagnostic"]["ABRUPT"]["incidents"] == 1


def test_incidents_outside_the_split_are_ignored():
    metas = [_pre("r9", "i1", 5)]
    cls = ev.classify_alerts([], metas, H, {"r0"})
    assert ev.incident_block(metas, cls, {"r0"})["primary_precursor"]["incidents"] == 0


def test_wilson_interval_handles_empty_and_bounds():
    assert ev.wilson(0, 0) is None
    lo, hi = ev.wilson(0, 10)
    assert lo == 0.0 and 0.2 < hi < 0.35


def test_alert_summary_counts_strict_false_alarms_and_host_days():
    s = ev.alert_summary(["hit", "false_alarm", "coincident_unpredictable", "duplicate"], host_days=2.0)
    assert s["alerts"] == 4 and s["strict_false_alarms"] == 2 and s["spec_false_alarms"] == 1
    assert s["strict_false_alarms_per_host_day"] == 1.0
    assert s["precision_strict"] == 0.25 and s["precision_lenient"] == 0.5


# ---------------------------------------------------------------------------------------------- persistence
def test_results_are_json_serialisable_and_numpy_safe():
    out = json.dumps({"a": np.float64(0.5), "b": np.int64(3), "c": np.array([1, 2])}, default=ev._jsonable)
    assert json.loads(out) == {"a": 0.5, "b": 3, "c": [1, 2]}
