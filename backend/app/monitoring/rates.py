"""Throughput from cumulative counters (telemetry contract: kind=counter is a running total that may reset to a lower value).

A rate is derived only from two consecutive valid samples of one series. No rate (None) is produced when:
  * the counter went down (reset on reboot / agent restart): the interval is skipped, never plotted as a huge or negative spike;
  * the interval is zero or negative (duplicate or out-of-order timestamps);
  * the interval is longer than MAX_RATE_GAP (samples were missed; averaging across a hole would invent a smooth rate).
The same rules are applied in SQL for time series (see queries.series_rates); this function serves the latest-value path.
"""
from __future__ import annotations

from datetime import datetime, timedelta

MAX_RATE_GAP = timedelta(seconds=900)


def counter_rate(prev_ts: datetime, prev_value: float, ts: datetime, value: float) -> float | None:
    seconds = (ts - prev_ts).total_seconds()
    if seconds <= 0 or seconds > MAX_RATE_GAP.total_seconds():
        return None
    if value < prev_value:
        return None
    return (value - prev_value) / seconds
