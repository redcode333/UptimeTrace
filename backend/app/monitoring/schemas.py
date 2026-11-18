"""Pydantic response models for the read API. UTC timestamps serialise as RFC 3339 with a trailing Z."""
from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel

HealthStatus = Literal["healthy", "warning", "critical", "offline", "unknown"]
Source = Literal["live", "synthetic", "replay"]


class ReasonOut(BaseModel):
    metric: str
    labels: dict[str, str]
    value: float
    threshold: float
    level: Literal["warning", "critical"]


class HealthOut(BaseModel):
    status: HealthStatus
    reasons: list[ReasonOut]


class MetricSampleOut(BaseModel):
    name: str
    kind: Literal["gauge", "counter"]
    unit: str
    labels: dict[str, str]
    value: float
    observed_at: datetime
    # True when the sample is older than STALE_AFTER relative to the host's last envelope (not used for health).
    stale: bool
    # Counters only: bytes (or units) per second from the two newest consecutive valid samples, else null.
    rate_per_second: float | None = None


class LatestSummary(BaseModel):
    """Headline numbers. A field is null when that measurement is not available; null is never zero."""

    cpu_percent: float | None
    memory_percent: float | None
    disk_percent_max: float | None
    disk_mount_max: str | None
    network_rx_bytes_per_second: float | None  # sum over reported interfaces
    network_tx_bytes_per_second: float | None


class HostOut(BaseModel):
    host_id: str
    hostname: str
    agent_id: str
    service: str | None
    source: Source
    # True only for source == "live". Synthetic and replay hosts are never connected infrastructure.
    connected_infrastructure: bool
    first_seen: datetime
    last_seen: datetime
    last_seen_age_seconds: float
    reporting_status: Literal["reporting", "silent", "replay"]
    health: HealthOut
    latest: LatestSummary


class HostDetailOut(HostOut):
    metrics: list[MetricSampleOut]


class RuleOut(BaseModel):
    warning: float
    critical: float


class ThresholdsOut(BaseModel):
    offline_after_seconds: float
    stale_after_seconds: float
    rules: dict[str, RuleOut]


class FacetsOut(BaseModel):
    # Each facet ignores its own filter (so the counts for the other options stay visible).
    source: dict[str, int]
    health: dict[str, int]


class HostListOut(BaseModel):
    items: list[HostOut]
    page: int
    page_size: int
    total: int
    facets: FacetsOut
    thresholds: ThresholdsOut


class LatestMetricsOut(BaseModel):
    host_id: str
    source: Source
    last_seen: datetime
    metrics: list[MetricSampleOut]


class SeriesPointOut(BaseModel):
    t: datetime
    value: float | None  # null = no data in this bucket (a gap, never zero)
    samples: int


class SeriesOut(BaseModel):
    labels: dict[str, str]
    unit: str
    value_type: Literal["gauge", "counter_rate"]
    points: list[SeriesPointOut]


class SeriesResponse(BaseModel):
    host_id: str
    source: Source
    metric: str
    kind: Literal["gauge", "counter"] | None  # null when the host has no samples of this metric in the window
    start: datetime
    end: datetime
    step_seconds: int
    agg: Literal["avg", "min", "max", "last"]
    series: list[SeriesOut]
