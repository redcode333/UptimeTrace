"""HTTP layer of the read API. Handlers are plain `def` so FastAPI runs the blocking SQLAlchemy work in its threadpool."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Callable, Literal

from fastapi import APIRouter, Path, Query
from sqlalchemy.orm import Session, sessionmaker

from app import errors
from app.errors import ErrorDetail
from app.monitoring import health as H
from app.monitoring import queries as Q
from app.monitoring import service as S
from app.monitoring.schemas import (
    FacetsOut, HostDetailOut, HostListOut, LatestMetricsOut, RuleOut, SeriesResponse, ThresholdsOut,
)
from app.monitoring.sources import SOURCES, classify_source

METRIC_NAME = r"^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)+$"
HOST_ID = r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$"
HEALTH_VALUES = ("healthy", "warning", "critical", "offline", "unknown")
DEFAULT_RANGE = timedelta(hours=6)


def _csv(values: list[str] | None, allowed: tuple[str, ...], field: str) -> set[str] | None:
    """`?source=live,synthetic` and `?source=live&source=synthetic` both work. Unknown values are a 422, never ignored."""
    if not values:
        return None
    items = {part.strip().lower() for v in values for part in v.split(",") if part.strip()}
    bad = sorted(items - set(allowed))
    if bad:
        raise errors.invalid_request([ErrorDetail(field, f"Unknown value(s) {bad}; allowed: {list(allowed)}.")])
    return items or None


def _aware(value: datetime, field: str) -> datetime:
    if value.tzinfo is None:
        raise errors.invalid_request([ErrorDetail(field, "Timestamp must include a UTC offset, e.g. 2026-11-11T12:00:00Z.")])
    return value.astimezone(UTC)


def thresholds() -> ThresholdsOut:
    return ThresholdsOut(
        offline_after_seconds=H.OFFLINE_AFTER.total_seconds(),
        stale_after_seconds=H.STALE_AFTER.total_seconds(),
        rules={name: RuleOut(warning=w, critical=c) for name, (w, c) in H.THRESHOLDS.items()},
    )


def build_router(session_factory: sessionmaker[Session], clock: Callable[[], datetime], dependencies: list | None = None) -> APIRouter:
    router = APIRouter(prefix="/api/v1", dependencies=dependencies or [])

    def load(session: Session, host_id: str | None = None, names: list[str] | None = None):
        now = clock()
        hosts = Q.fetch_hosts(session, host_id)
        return now, hosts, Q.fetch_latest(session, hosts, names)

    @router.get("/hosts", response_model=HostListOut)
    def list_hosts(
        search: str | None = Query(None, max_length=128, description="Case-insensitive substring of hostname or host_id."),
        source: list[str] | None = Query(None),
        health: list[str] | None = Query(None),
        sort: Literal["last_seen", "hostname"] = "last_seen",
        order: Literal["asc", "desc"] | None = None,
        page: int = Query(1, ge=1),
        page_size: int = Query(50, ge=1, le=200),
    ):
        sources = _csv(source, SOURCES, "source")
        healths = _csv(health, HEALTH_VALUES, "health")
        needle = (search or "").strip().lower() or None
        with session_factory() as session:
            now, hosts, latest = load(session, names=S.SUMMARY_METRICS)
        built = [S.build_host(h, latest[h.host_id], now) for h in hosts]
        if needle:
            built = [h for h in built if needle in h.host_id.lower() or needle in h.hostname.lower()]

        def count(items, attr):
            tally: dict[str, int] = {}
            for h in items:
                key = h.source if attr == "source" else h.health.status
                tally[key] = tally.get(key, 0) + 1
            return tally

        def only(items, srcs, hs):
            return [h for h in items if (srcs is None or h.source in srcs) and (hs is None or h.health.status in hs)]

        facets = FacetsOut(
            source={k: count(only(built, None, healths), "source").get(k, 0) for k in SOURCES},
            health={k: count(only(built, sources, None), "health").get(k, 0) for k in HEALTH_VALUES},
        )
        matched = only(built, sources, healths)
        descending = (order or ("desc" if sort == "last_seen" else "asc")) == "desc"
        if sort == "hostname":
            matched.sort(key=lambda h: (h.hostname.casefold(), h.host_id), reverse=descending)
        else:
            matched.sort(key=lambda h: (h.last_seen, h.host_id), reverse=descending)
        offset = (page - 1) * page_size
        return HostListOut(
            items=matched[offset: offset + page_size], page=page, page_size=page_size, total=len(matched),
            facets=facets, thresholds=thresholds(),
        )

    @router.get("/hosts/{host_id}", response_model=HostDetailOut)
    def get_host(host_id: str = Path(pattern=HOST_ID)):
        with session_factory() as session:
            now, hosts, latest = load(session, host_id)
        if not hosts:
            raise errors.host_not_found(host_id)
        return S.build_host(hosts[0], latest[host_id], now, with_metrics=True)

    @router.get("/hosts/{host_id}/metrics/latest", response_model=LatestMetricsOut)
    def latest_metrics(host_id: str = Path(pattern=HOST_ID)):
        with session_factory() as session:
            now, hosts, latest = load(session, host_id)
        if not hosts:
            raise errors.host_not_found(host_id)
        row = hosts[0]
        built = S.build_host(row, latest[host_id], now, with_metrics=True)
        return LatestMetricsOut(host_id=host_id, source=built.source, last_seen=row.last_seen, metrics=built.metrics)

    @router.get("/hosts/{host_id}/metrics/series", response_model=SeriesResponse)
    def metric_series(
        host_id: str = Path(pattern=HOST_ID),
        metric: str = Query(..., max_length=128, pattern=METRIC_NAME),
        start: datetime | None = None,
        end: datetime | None = None,
        step: int | None = Query(None, description="Bucket size in seconds; omitted = chosen from the range."),
        agg: Literal["avg", "min", "max", "last"] = "avg",
    ):
        end_t = _aware(end, "end") if end else clock()
        start_t = _aware(start, "start") if start else end_t - DEFAULT_RANGE
        with session_factory() as session:
            if not Q.fetch_hosts(session, host_id):
                raise errors.host_not_found(host_id)
            return S.load_series(session, host_id, metric, start_t, end_t, step, agg)

    return router
