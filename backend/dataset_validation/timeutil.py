"""Unit-independent time conversion. pandas may keep parquet timestamps in microseconds, so integer views are not seconds."""
from __future__ import annotations

import numpy as np
import pandas as pd

_EPOCH = pd.Timestamp("1970-01-01", tz="UTC")


def epoch_seconds(values) -> np.ndarray:
    """Seconds since the Unix epoch as float64, for any datetime-like series or array (naive values are read as UTC)."""
    s = pd.Series(pd.to_datetime(values, utc=True))
    return ((s - _EPOCH) / pd.Timedelta(seconds=1)).to_numpy(dtype="float64")
