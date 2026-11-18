"""Pure helpers behind the AC-15 distinctness checks (spec section 17)."""
from __future__ import annotations

import numpy as np
import pytest

from simulator_v2.distinct import CHI2_ALPHA, CV_MIN, coeff_var, chi2_uniform, jaccard


def test_chi_squared_accepts_uniform_and_rejects_a_skewed_time_of_day_histogram():
    rng = np.random.default_rng(0)
    uniform = np.bincount(rng.integers(0, 24, 1300), minlength=24)
    stat, p = chi2_uniform(uniform)
    assert p > CHI2_ALPHA
    skewed = np.bincount(np.concatenate([rng.integers(0, 12, 1100), rng.integers(12, 24, 200)]), minlength=24)
    assert chi2_uniform(skewed)[1] < 1e-6
    assert chi2_uniform(np.full(24, 50))[0] == 0.0 and chi2_uniform(np.full(24, 50))[1] == pytest.approx(1.0)


def test_jaccard_overlap():
    assert jaccard({1, 2, 3}, {3, 4}) == pytest.approx(0.25) and jaccard(set(), set()) == 0.0
    assert jaccard({(1, 5)}, {(2, 5)}) == 0.0


def test_spec_profile_ranges_clear_the_cv_minimum():
    rng = np.random.default_rng(1)
    for lo, hi in ((8, 40), (25, 70), (30, 75), (15, 70), (0.3, 0.9), (0.05, 0.3), (0, 1.5), (0.05, 1.0), (0.1, 2.0), (0.03, 0.12)):
        assert coeff_var(rng.uniform(lo, hi, 5000)) > CV_MIN, (lo, hi)
    assert coeff_var(np.full(100, 3.0)) == 0.0 < CV_MIN        # a degenerate parameter would be caught
