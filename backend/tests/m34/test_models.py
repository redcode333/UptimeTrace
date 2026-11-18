"""M3.4 focused checks: eligibility, validation-only thresholds, independent model loading, output shape/finiteness, schema consistency."""
from __future__ import annotations

import glob
import json
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest

from ml import build_features as bf
from ml import features as ft
from ml import train_baselines as tb
from ml.predict import SchemaError, load_model

ART = Path(__file__).resolve().parents[3] / "artifacts"
BACKEND = Path(__file__).resolve().parents[2]
KINDS = ("rule", "logreg", "xgboost")


def test_alert_accounting_uses_900s_dedup_and_counts_only_alerts_with_no_incident_ahead():
    run = ["a"] * 40
    di = list(range(40))
    score = np.zeros(40); score[[3, 4, 5, 18, 19, 40 - 1]] = 1.0                 # candidates at rows 3,4,5 (one burst), 18,19, 39
    nin = np.zeros(40, dtype=int); nin[18:20] = 2                                # an incident begins within H of rows 18, 19
    a = tb.alert_stats(run, di, score, 0.5, nin, delta_s=60)
    assert a["alerts"] == 3                                                       # 3 fires; 4,5 suppressed (<15 rows); 18 fires at 15 rows after 3; 19 suppressed; 39 fires
    assert a["false_alarms"] == 2                                                 # rows 3 and 39; the alert at row 18 has an incident ahead
    assert a["evaluable_host_days"] == pytest.approx(40 * 60 / 86400, abs=1e-3)
    assert a["false_alarms_per_evaluable_host_day"] == pytest.approx(2 / (40 * 60 / 86400), rel=0.05)
    assert tb.alert_stats(["a", "b"], [0, 0], np.ones(2), 0.5, np.zeros(2, dtype=int))["alerts"] == 2     # dedup is per run


def test_training_view_drops_unscorable_and_labels_only_precursor_windows_positive():
    keep, y = tb.eligible_view(["negative", "positive", "unscorable", "negative", "unscorable", "positive"])
    assert list(keep) == [True, True, False, True, False, True] and list(y) == [0, 1, 0, 1]
    assert y.dtype == np.int8


def test_threshold_is_the_f1_optimum_of_the_data_it_is_given_and_metrics_are_consistent():
    y = np.array([0, 0, 0, 0, 0, 0, 1, 1, 1, 0])
    s = np.array([.1, .2, .15, .3, .25, .35, .9, .8, .4, .5])
    thr, f1 = tb.choose_threshold(y, s)
    m = tb.metrics(y, s, thr)
    c = m["confusion"]
    assert c["tp"] + c["fp"] + c["fn"] + c["tn"] == len(y) and m["positives"] == 3 and m["negatives"] == 7
    assert m["false_positives"] == c["fp"] and m["false_negatives"] == c["fn"]
    assert m["f1"] == pytest.approx(f1, abs=1e-3)
    for other in np.unique(s):                                       # no other threshold does better on these data
        assert tb.metrics(y, s, other)["f1"] <= m["f1"] + 1e-9
    assert 0 < m["pr_auc"] <= 1


def _dirs():
    f = sorted(glob.glob(str(ART / "features" / "uptimetrace-m31-seed0-*")))
    l = sorted(glob.glob(str(ART / "labels" / "uptimetrace-m31-seed0-*")))
    if not (f and l):
        pytest.skip("seed-0 features/labels not present")
    return Path(f[0]), Path(l[0])


@pytest.fixture(scope="module")
def trained(tmp_path_factory):
    fdir, ldir = _dirs()
    requested = []
    real = bf.load_xy

    def spy(feature_dir, labels_dir, splits, horizon_s=1800, **kw):
        requested.append(tuple(splits))
        return real(feature_dir, labels_dir, splits, horizon_s, **kw)
    bf.load_xy = spy
    try:
        out = tb.train_all(fdir, ldir, tmp_path_factory.mktemp("models"), 1800)
    finally:
        bf.load_xy = real
    return {"out": out, "requested": requested, "fdir": fdir, "ldir": ldir}


def test_only_train_and_validation_splits_are_ever_loaded(trained):
    assert set(trained["requested"]) == {("train",), ("val",)}
    with pytest.raises(AssertionError):
        tb.load_view(trained["fdir"], trained["ldir"], "test_iid", 1800)
    with pytest.raises(AssertionError):
        tb.load_view(trained["fdir"], trained["ldir"], "test_ood", 1800)
    rep = json.loads((trained["out"] / "report.json").read_text())
    assert rep["test_splits_used"] is False and "PRELIMINARY" in rep["status"]


def test_models_load_in_a_fresh_process_without_the_training_code_and_score_correctly(trained):
    xy = bf.load_xy(trained["fdir"], trained["ldir"], ["val"], 1800)
    X = xy["X"][:500]
    np.save(trained["out"] / "_probe.npy", X)
    code = (
        "import sys, json, numpy as np\n"
        "from ml.predict import load_model\n"
        "assert 'ml.train_baselines' not in sys.modules and 'ml.build_features' not in sys.modules\n"
        "X = np.load(sys.argv[2]); out = {}\n"
        "for k in ('rule', 'logreg', 'xgboost'):\n"
        "    r = load_model(sys.argv[1] + '/' + k).predict_risk(X)\n"
        "    out[k] = r.tolist()\n"
        "print(json.dumps(out))\n")
    p = subprocess.run([sys.executable, "-c", code, str(trained["out"]), str(trained["out"] / "_probe.npy")], cwd=BACKEND,
                       capture_output=True, text=True, timeout=300)
    assert p.returncode == 0, p.stderr[-800:]
    got = json.loads(p.stdout.strip().splitlines()[-1])
    for k in KINDS:
        r = np.array(got[k])
        assert r.shape == (500,) and np.isfinite(r).all() and r.min() >= 0 and r.max() <= 1
        assert np.allclose(r, load_model(trained["out"] / k).predict_risk(X))        # same scores in-process


def test_schema_matches_the_feature_pipeline_and_input_order_does_not_matter(trained):
    xy = bf.load_xy(trained["fdir"], trained["ldir"], ["val"], 1800)
    X = xy["X"][:200]
    for k in KINDS:
        m = load_model(trained["out"] / k)
        s = m.schema
        assert s["feature_names"] == ft.NAMES and s["feature_version"] == ft.VERSION and s["horizon_s"] == 1800
        assert s["model_input_features"] == [n for n in ft.NAMES if n not in s["excluded_features"]] and "elapsed_s" in s["excluded_features"]
        assert s["threshold_chosen_on"] == "val" and s["preliminary"] is True and 0 <= s["threshold"] <= 1
        base = m.predict_risk(X)
        perm = np.random.default_rng(0).permutation(len(ft.NAMES))
        shuffled = m.predict_risk(X[:, perm], [ft.NAMES[i] for i in perm])
        assert np.allclose(base, shuffled)                                         # named columns are reordered to the schema
        with pytest.raises(SchemaError):
            m.predict_risk(X[:, :-1])                                              # wrong number of columns
        with pytest.raises(SchemaError):
            m.predict_risk(X, ["wrong"] * len(ft.NAMES))                           # wrong names
        with pytest.raises(SchemaError):
            m.predict_risk(X[0])                                                   # not 2-D
        alert = m.alert(X)
        assert alert.dtype == bool and alert.shape == (200,) and (alert == (base >= m.threshold)).all()


def test_scores_stay_finite_for_missing_and_extreme_inputs(trained):
    n = len(ft.NAMES)
    weird = np.vstack([np.full(n, np.nan), np.full(n, np.inf), np.full(n, -np.inf), np.full(n, 1e30), np.zeros(n)])
    for k in KINDS:
        r = load_model(trained["out"] / k).predict_risk(weird)
        assert r.shape == (5,) and np.isfinite(r).all() and r.min() >= 0 and r.max() <= 1


def test_rule_is_deterministic_and_reads_only_its_utilisation_features(trained):
    m = load_model(trained["out"] / "rule")
    n = len(ft.NAMES)
    X = np.zeros((2, n))
    X[0, ft.INDEX["mem_last"]] = 93.0
    X[1, ft.INDEX["cpu_mean_300"]] = 40.0
    X[1, ft.INDEX["rx_rate_last"]] = 1e12                      # a feature outside the rule must not matter
    r = m.predict_risk(X)
    assert r[0] == pytest.approx(0.93) and r[1] == pytest.approx(0.40)
    assert np.array_equal(r, m.predict_risk(X))


def test_report_has_the_required_content_and_marks_results_preliminary(trained):
    rep = json.loads((trained["out"] / "report.json").read_text())
    assert rep["train"]["positive"] > 0 and rep["val"]["positive"] > 0 and rep["val"]["unscorable_dropped"] > 0
    for k in KINDS:
        v = rep["models"][k]["val"]
        assert set(v) >= {"precision", "recall", "pr_auc", "confusion", "false_positives", "false_negatives", "positives", "negatives"}
        assert rep["models"][k]["train_seconds"] >= 0 and rep["models"][k]["inference_seconds_val"] > 0
        assert v["positives"] == rep["val"]["positive"] and v["negatives"] == rep["val"]["negative"]
    md = (trained["out"] / "report.md").read_text()
    assert "PRELIMINARY" in md and "optimistic" in md
