"""Turns query rows into API objects. No SQL here, no HTTP here."""
from __future__ import annotations

import math
from datetime import UTC, datetime, timedelta

from typing import Any, cast

from sqlalchemy.orm import Session

from app import errors
from app.errors import ErrorDetail

from app.monitoring import health as H
from app.monitoring import queries as Q
from app.monitoring.rates import counter_rate
from app.monitoring.settings import DEFAULT_CONFIG, MonitoringConfig
from app.monitoring.schemas import (
    HealthOut, HostDetailOut, HostOut, LatestSummary, MetricSampleOut, ReasonOut, SeriesOut, SeriesPointOut, SeriesResponse,
)
from app.monitoring.sources import classify_source

CPU = "system.cpu.utilization"
MEM = "system.memory.utilization"
DISK = "system.disk.utilization"
RX = "system.network.rx_bytes"
TX = "system.network.tx_bytes"
SUMMARY_METRICS = [CPU, MEM, DISK, RX, TX]

AUTO_STEPS = [60, 120, 300, 600, 900, 1800, 3600, 7200, 14400, 21600, 43200, 86400]
AUTO_TARGET_POINTS = 720
MAX_POINTS = 1500
MIN_STEP = 10
MAX_RANGE = timedelta(days=31)


def _utc(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=UTC)


def sample_out(s: Q.SampleRow, last_seen: datetime) -> MetricSampleOut:
    rate = None
    if s.kind == "counter" and s.previous is not None:
        rate = counter_rate(s.previous[0], float(s.previous[1]), s.observed_at, float(s.value))
    return MetricSampleOut(
        name=s.name, kind=cast(Any, s.kind), unit=s.unit, labels=s.labels, value=float(s.value),
        observed_at=s.observed_at, stale=(last_seen - s.observed_at) > H.STALE_AFTER, rate_per_second=rate,
    )


def _summary(samples: list[MetricSampleOut]) -> LatestSummary:
    def one(name):
        vals = [s for s in samples if s.name == name and not s.stale]
        return vals[0].value if vals else None

    disks = [s for s in samples if s.name == DISK and not s.stale]
    worst = max(disks, key=lambda s: s.value) if disks else None

    def rate_sum(name):
        rates = [s.rate_per_second for s in samples if s.name == name and not s.stale and s.rate_per_second is not None]
        return sum(rates) if rates else None

    return LatestSummary(
        cpu_percent=one(CPU), memory_percent=one(MEM),
        disk_percent_max=worst.value if worst else None,
        disk_mount_max=(worst.labels.get("mount") if worst else None),
        network_rx_bytes_per_second=rate_sum(RX), network_tx_bytes_per_second=rate_sum(TX),
    )


def build_host(row: Q.HostRow, samples: list[Q.SampleRow], now: datetime, cfg: MonitoringConfig = DEFAULT_CONFIG, with_metrics: bool = False):
    source = classify_source(row.host_id)
    outs = [sample_out(s, row.last_seen) for s in samples]
    verdict = H.classify(source, row.last_seen, now, samples, cfg)
    health = HealthOut(
        status=cast(Any, verdict.status), category=cast(Any, verdict.category),
        reasons=[ReasonOut(metric=r.metric, labels=r.labels, value=r.value, threshold=r.threshold, level=cast(Any, r.level), key=r.key)
                 for r in verdict.reasons],
    )
    common: dict[str, Any] = dict(
        host_id=row.host_id, hostname=row.hostname, agent_id=row.agent_id, service=row.service, source=source,
        connected_infrastructure=(source == "live"), first_seen=row.first_seen, last_seen=row.last_seen,
        last_seen_age_seconds=max(0.0, (now - row.last_seen).total_seconds()),
        reporting_status=H.reporting_status(source, row.last_seen, now, cfg), health=health, latest=_summary(outs),
    )
    return HostDetailOut(**common, metrics=outs) if with_metrics else HostOut(**common)


def choose_step(start: datetime, end: datetime, step: int | None) -> int:
    span = (end - start).total_seconds()
    if step is not None:
        return step
    for candidate in AUTO_STEPS:
        if span / candidate <= AUTO_TARGET_POINTS:
            return candidate
    return AUTO_STEPS[-1]


def build_series(buckets: list[Q.SeriesBucket], kind: str, unit: str, start: datetime, step: int, n: int, agg: str) -> list[SeriesOut]:
    """Dense grid: one point per bucket, value null where the window has no data."""
    by_labels: dict[str, tuple[dict, dict[int, Q.SeriesBucket]]] = {}
    for b in buckets:
        key = repr(sorted(b.labels.items()))
        by_labels.setdefault(key, (b.labels, {}))[1][b.bucket] = b
    out = []
    for labels, cells in by_labels.values():
        points = []
        for i in range(n):
            cell = cells.get(i)
            points.append(SeriesPointOut(
                t=start + timedelta(seconds=i * step),
                value=(getattr(cell, agg) if cell else None),
                samples=(cell.samples if cell else 0),
            ))
        out.append(SeriesOut(
            labels=labels,
            unit=(f"{unit}_per_second" if kind == "counter" else unit),
            value_type=("counter_rate" if kind == "counter" else "gauge"),
            points=points,
        ))
    return out


def bucket_count(start: datetime, end: datetime, step: int) -> int:
    return math.ceil((end - start).total_seconds() / step)


def load_series(session: Session, host_id: str, metric: str, start_t: datetime, end_t: datetime,
                step: int | None, agg: str, max_points: int = MAX_POINTS) -> SeriesResponse:
    """Validated, bounded metric series for one host. Shared by the console API and the MCP tools (one implementation).

    The caller must already have established that the host exists AND that the caller may see it.
    """
    problems: list[ErrorDetail] = []
    if start_t >= end_t:
        problems.append(ErrorDetail("start", "start must be earlier than end."))
    elif end_t - start_t > MAX_RANGE:
        problems.append(ErrorDetail("start", f"The range may not exceed {MAX_RANGE.days} days."))
    if step is not None and step < MIN_STEP:
        problems.append(ErrorDetail("step", f"step must be at least {MIN_STEP} seconds."))
    if problems:
        raise errors.invalid_request(problems)
    bucket = choose_step(start_t, end_t, step)
    n = bucket_count(start_t, end_t, bucket)
    if n > max_points:
        raise errors.invalid_request([ErrorDetail("step", f"{n} points requested; the maximum is {max_points}. Use a larger step.")])
    lo, hi = start_t - Q.SKEW, end_t + Q.LATE_ARRIVAL_GRACE
    found = Q.metric_kind(session, host_id, metric, lo, hi)
    kind, unit, series = (None, "", [])
    if found:
        kind, unit = found
        rows = Q.series(session, host_id, metric, kind, start_t, end_t, bucket)
        series = build_series(rows, kind, unit, start_t, bucket, n, agg)
    return SeriesResponse(host_id=host_id, source=cast(Any, classify_source(host_id)), metric=metric, kind=cast(Any, kind), start=start_t,
                          end=end_t, step_seconds=bucket, agg=cast(Any, agg), series=series)
