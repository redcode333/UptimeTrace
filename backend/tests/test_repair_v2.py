"""Repairs to the simulator v2 derived data: crash-safe writes, label views, and the feature registry.

No database and no dataset are needed. The writer tests inject real failures (a truncated write, a damaged read-back) and check
that nothing partial is ever visible under the final name.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from ml import build_labels as bl
from ml import features as ft
from ml import io_safe
from ml.io_safe import VerificationError, verify_parquet_file, write_parquet_verified


def _table(n: int = 500) -> pa.Table:
    rng = np.random.default_rng(0)
    return pa.table({"run_id": pa.array(["r0001"] * n).dictionary_encode(), "x": pa.array(rng.normal(size=n).astype(np.float32)),
                     "nan_col": pa.array(np.where(np.arange(n) % 7 == 0, np.nan, 1.0).astype(np.float32)),
                     "decision_index": pa.array(np.arange(n, dtype=np.int32))})


# ---------------------------------------------------------------------------------------------- crash-safe writes
def test_a_verified_write_publishes_a_readable_file_with_its_digest(tmp_path):
    final = tmp_path / "run=r0001.parquet"
    digest = write_parquet_verified(_table(), final)
    assert final.exists() and len(digest) == 64
    assert verify_parquet_file(final, expected_rows=500, required_columns=["x", "nan_col"])["sha256"] == digest
    assert not [p for p in tmp_path.iterdir() if p.name.startswith(".tmp-")]


def test_nan_values_survive_read_back_as_verified_values_not_as_mismatches(tmp_path):
    final = tmp_path / "f.parquet"
    write_parquet_verified(_table(), final)
    back = pq.read_table(final).to_pandas()
    assert back["nan_col"].isna().sum() == np.count_nonzero(np.arange(500) % 7 == 0)


def test_a_truncated_write_never_appears_under_the_final_name(tmp_path, monkeypatch):
    final = tmp_path / "run=r0002.parquet"
    real_write = pq.write_table

    def truncating_write(table, where, **kw):
        real_write(table, where, **kw)
        with open(where, "r+b") as f:                 # what a crash mid-write leaves behind
            f.truncate(max(64, f.seek(0, 2) // 2))

    monkeypatch.setattr(io_safe.pq, "write_table", truncating_write)
    with pytest.raises(Exception):                    # the read-back refuses the damaged temporary file
        write_parquet_verified(_table(), final)
    assert not final.exists()
    assert not [p for p in tmp_path.iterdir()], "the temporary file must be removed after a failed write"


def test_a_damaged_read_back_is_refused_and_leaves_nothing_behind(tmp_path, monkeypatch):
    final = tmp_path / "run=r0003.parquet"
    real_pf = pq.ParquetFile

    class DamagedReader:
        def __init__(self, path, *a, **kw):
            self._pf = real_pf(path, *a, **kw)

        def read(self, *a, **kw):
            t = self._pf.read(*a, **kw)
            col = t.column("x").to_numpy().copy()
            col[10] += 1.0
            return t.set_column(t.column_names.index("x"), "x", pa.array(col))

    monkeypatch.setattr(io_safe.pq, "ParquetFile", DamagedReader)
    with pytest.raises(VerificationError, match="values differ"):
        write_parquet_verified(_table(), final)
    assert not final.exists() and not list(tmp_path.iterdir())


def test_row_count_and_required_columns_are_checked_by_the_independent_verifier(tmp_path):
    final = tmp_path / "f.parquet"
    write_parquet_verified(_table(), final)
    with pytest.raises(VerificationError, match="rows"):
        verify_parquet_file(final, expected_rows=499)
    with pytest.raises(VerificationError, match="missing columns"):
        verify_parquet_file(final, required_columns=["not_there"])


# ------------------------------------------------------------------------------------------ label views (the repair)
def test_each_label_class_maps_to_the_right_primary_and_operational_values():
    # 4 decisions: excluded, positive (PRECURSOR in window), unscorable (ABRUPT-only in window), negative (no onset)
    evaluable = np.array([False, True, True, True])
    n_in = np.array([0, 2, 1, 0])
    any_p = np.array([False, True, False, False])
    cls, scored, target, operational = bl.label_views(evaluable, n_in, any_p)
    assert list(cls) == ["excluded", "positive", "unscorable", "negative"]
    # Primary view: only positive and negative are scored; unscorable is masked, excluded has no label.
    assert list(scored) == [False, True, False, True]
    assert list(target[scored]) == [1, 0]
    # Secondary operational view: any incident onset in the window is 1, for evaluable decisions only.
    assert list(operational[evaluable]) == [1, 1, 0]


def test_unscorable_decisions_are_never_converted_into_positive_examples():
    evaluable = np.ones(3, dtype=bool)
    n_in = np.array([1, 3, 2])
    any_p = np.array([False, False, False])          # only ABRUPT / SILENT onsets in every window
    cls, scored, target, _ = bl.label_views(evaluable, n_in, any_p)
    assert set(cls) == {"unscorable"}
    assert not scored.any(), "no unscorable decision may enter the primary scored view"


def test_excluded_decisions_carry_no_primary_or_operational_label():
    evaluable = np.zeros(4, dtype=bool)
    n_in = np.array([2, 0, 1, 0])
    any_p = np.array([True, False, False, False])
    cls, scored, _, operational = bl.label_views(evaluable, n_in, any_p)
    assert set(cls) == {"excluded"} and not scored.any()
    assert not operational[~evaluable].any()


def test_label_class_rule_matches_the_protocol_definition():
    from ml import labels as lb
    n_in = np.array([0, 1, 1, 2])
    any_p = np.array([False, True, False, True])
    assert list(lb.label_class(n_in, any_p)) == ["negative", "positive", "unscorable", "positive"]


# ------------------------------------------------------------------------------------- feature definitions (the repair)
def test_the_four_unsupported_rate_features_are_removed_and_the_others_kept():
    assert ft.UNSUPPORTED == {"rx_rate_std_300", "rx_rate_slope_300", "tx_rate_std_300", "tx_rate_slope_300"}
    for name in ft.UNSUPPORTED:
        assert name not in ft.NAMES and name not in ft.META
    assert "rx_rate_std_900" in ft.NAMES and "tx_rate_slope_3600" in ft.NAMES
    assert ft.F == 192


def _series(hours: float = 4.0, cadence: float = 60.0, seed: int = 0):
    rng = np.random.default_rng(seed)
    tau = np.arange(0.0, hours * 3600.0, cadence) + rng.uniform(-2, 2, int(hours * 3600 / cadence))
    tau = np.sort(tau)
    env = tau + 0.2
    out = {}
    for s in ft.ALL_SERIES:
        if s in ft.PCT:
            val = rng.uniform(20, 80, tau.size)
        else:
            val = np.cumsum(rng.uniform(1e5, 2e5, tau.size))
        out[s] = (tau, env, val)
    return out


def test_no_rate_std_or_slope_is_claimed_at_300_seconds_with_sixty_second_sampling():
    series = _series()
    t = np.array([3 * 3600.0, 3 * 3600.0 + 900.0])
    X = ft.compute_features(series, t)
    assert X.shape == (2, ft.F)
    assert "rx_rate_std_300" not in ft.INDEX


def test_longer_rate_windows_still_produce_finite_spreads():
    series = _series()
    t = np.array([3 * 3600.0])
    X = ft.compute_features(series, t)
    assert np.isfinite(X[0, ft.INDEX["rx_rate_std_900"]])
    assert np.isfinite(X[0, ft.INDEX["tx_rate_mean_900"]])


def test_at_exact_sixty_second_sampling_a_300_second_window_holds_only_four_pairs():
    """Without jitter a 300-second window holds 5 observations, hence 4 consecutive pairs: short of the 5 the removed features need."""
    tau = np.arange(0.0, 4 * 3600.0, 60.0)
    rng = np.random.default_rng(1)
    series = {s: (tau, tau + 0.2, (rng.uniform(20, 80, tau.size) if s in ft.PCT else np.cumsum(rng.uniform(1e5, 2e5, tau.size))))
              for s in ft.ALL_SERIES}
    t = np.arange(3600.0, 3 * 3600.0, 60.0)
    X = ft.compute_features(series, t)
    assert np.nanmax(X[:, ft.INDEX["rx_pairs_300"]]) <= 4.0
