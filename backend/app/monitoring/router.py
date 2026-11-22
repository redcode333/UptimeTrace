"""HTTP layer of the read API. Handlers are plain `def` so FastAPI runs the blocking SQLAlchemy work in its threadpool."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Callable, Literal

from fastapi import APIRouter, Path, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session, sessionmaker

from app import errors
from app.errors import ErrorDetail
from app.monitoring import queries as Q
from app.monitoring import service as S
from app.monitoring.schemas import (
    GroupRefOut,
    FacetsOut, HostDetailOut, HostListOut, LatestMetricsOut, RuleOut, SeriesResponse, ThresholdsOut,
)
from app.monitoring.settings import DEFAULT_CONFIG, SAMPLE_STALE_AFTER, MonitoringConfig, load as load_settings
from app.monitoring import health as H
from app.monitoring.sources import SOURCES, classify_source
from app.registry.service import default_workspace_id

METRIC_NAME = r"^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)+$"
HOST_ID = r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$"
HEALTH_VALUES = ("healthy", "warning", "critical", "stale", "offline", "unknown")
ATTENTION_RANK = {"critical": 0, "warning": 1, "stale": 2, "offline": 3, "unknown": 4, "healthy": 5}
# Utilisation distribution buckets: [lo, hi) in percent, the same edges the console has always drawn.
DISTRIBUTION_BUCKETS = [(0, 20), (20, 40), (40, 60), (60, 80), (80, 101)]
DISTRIBUTION_METRICS = {"cpu": "system.cpu.utilization", "memory": "system.memory.utilization", "disk_max": "system.disk.utilization"}
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


def thresholds(cfg: MonitoringConfig = DEFAULT_CONFIG) -> ThresholdsOut:
    return ThresholdsOut(
        host_stale_after_seconds=cfg.stale_after.total_seconds(),
        offline_after_seconds=cfg.offline_after.total_seconds(),
        stale_after_seconds=SAMPLE_STALE_AFTER.total_seconds(),
        rules={name: RuleOut(warning=r.warning, critical=r.critical) for name, r in cfg.rules.items()},
    )


def _fresh(samples, name: str, last_seen: datetime, now: datetime):
    """Samples of one metric that count as current: not stale against the host's last report, and not older than the staleness
    window now. A silent host's old value is therefore not current. Missing values stay missing."""
    out = []
    for s in samples:
        if s.name != name:
            continue
        o = S.sample_out(s, last_seen)
        if not o.stale and (now - o.observed_at).total_seconds() <= H.STALE_AFTER.total_seconds():
            out.append(o)
    return out


def _placement(entry: dict | None) -> dict:
    """Registry placement as output models. A host with no registry row has no placement."""
    entry = entry or {"environment": None, "region": None, "cluster": None, "services": []}
    ref = lambda v: GroupRefOut(**v) if v else None  # noqa: E731
    return {"environment": ref(entry["environment"]), "region": ref(entry["region"]), "cluster": ref(entry["cluster"]),
            "services": [GroupRefOut(**x) for x in entry["services"]]}


def _in_groups(h, wanted: dict[str, set[str]]) -> bool:
    """A host matches each selected dimension when it belongs to one of the selected ids in that dimension (AND across dimensions)."""
    for key in ("environment", "region", "cluster"):
        placed = getattr(h, key)
        if wanted[key] and (placed is None or placed.id not in wanted[key]):
            return False
    if wanted["service"] and not any(s.id in wanted["service"] for s in h.services):
        return False
    return True


def _state(value: float | None, rule) -> str:
    """The same rule the console colours by: critical at or above the critical limit, warning at or above the warning limit."""
    if value is None or rule is None:
        return "none"
    if value >= rule.critical:
        return "critical"
    if value >= rule.warning:
        return "warning"
    return "normal"


class DistributionMaxOut(BaseModel):
    value: float
    host_id: str
    hostname: str


class DistributionOut(BaseModel):
    """Utilisation over the whole in-scope fleet: buckets, states and the highest host. Hosts with no current value are counted in
    `no_data`, never in a bucket. Every in-scope host is counted exactly once."""
    metric: str
    sources: list[str]
    total_in_scope: int
    bucket_labels: list[str]
    buckets: list[int]
    no_data: int
    states: dict[str, int]
    max: DistributionMaxOut | None
    complete: bool


class RankedHostOut(BaseModel):
    host_id: str
    hostname: str
    source: str
    value: float
    observed_at: datetime
    last_seen_age_seconds: float
    reporting_status: str


class RankingOut(BaseModel):
    metric: str
    sources: list[str]
    total_in_scope: int
    with_value: int
    without_value: int
    limit: int
    items: list[RankedHostOut]
    complete: bool


def build_router(session_factory: sessionmaker[Session], clock: Callable[[], datetime], dependencies: list | None = None) -> APIRouter:
    router = APIRouter(prefix="/api/v1", dependencies=dependencies or [])

    def load(session: Session, host_id: str | None = None, names: list[str] | None = None):
        now = clock()
        cfg = load_settings(session, default_workspace_id(session)).config
        hosts = Q.fetch_hosts(session, host_id)
        return now, hosts, Q.fetch_latest(session, hosts, names), cfg

    @router.get("/hosts", response_model=HostListOut)
    def list_hosts(
        search: str | None = Query(None, max_length=128, description="Case-insensitive substring of hostname or host_id."),
        source: list[str] | None = Query(None),
        health: list[str] | None = Query(None),
        environment: list[str] | None = Query(None, description="Registry environment ids."),
        region: list[str] | None = Query(None, description="Registry region ids."),
        cluster: list[str] | None = Query(None, description="Registry cluster ids."),
        service: list[str] | None = Query(None, description="Registry service ids; a host matches if it belongs to any of them."),
        sort: Literal["last_seen", "hostname", "attention"] = "last_seen",
        order: Literal["asc", "desc"] | None = None,
        page: int = Query(1, ge=1),
        page_size: int = Query(50, ge=1, le=200),
    ):
        sources = _csv(source, SOURCES, "source")
        healths = _csv(health, HEALTH_VALUES, "health")
        needle = (search or "").strip().lower() or None
        wanted = {"environment": set(environment or []), "region": set(region or []), "cluster": set(cluster or []), "service": set(service or [])}
        with session_factory() as session:
            now, hosts, latest, cfg = load(session, names=S.SUMMARY_METRICS)
            groups = Q.host_groups(session, default_workspace_id(session))
        built = [S.build_host(h, latest[h.host_id], now, cfg) for h in hosts]
        built = [h.model_copy(update=_placement(groups.get(h.host_id))) for h in built]
        if needle:
            built = [h for h in built if needle in h.host_id.lower() or needle in h.hostname.lower()]
        if any(wanted.values()):
            built = [h for h in built if _in_groups(h, wanted)]

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
            connected=sum(1 for h in built if H.is_connected(h.source, h.last_seen, now, cfg)),
        )
        matched = only(built, sources, healths)
        descending = (order or ("desc" if sort == "last_seen" else "asc")) == "desc"
        if sort == "hostname":
            matched.sort(key=lambda h: (h.hostname.casefold(), h.host_id), reverse=descending)
        elif sort == "attention":
            # Severity first (critical, warning, stale, offline, unknown, healthy), then hostname: the whole in-scope fleet is ordered.
            matched.sort(key=lambda h: (ATTENTION_RANK.get(h.health.status, 9), h.hostname.casefold(), h.host_id), reverse=descending)
        else:
            matched.sort(key=lambda h: (h.last_seen, h.host_id), reverse=descending)
        offset = (page - 1) * page_size
        return HostListOut(
            items=matched[offset: offset + page_size], page=page, page_size=page_size, total=len(matched),
            facets=facets, thresholds=thresholds(cfg),
        )

    @router.get("/hosts/distribution", response_model=DistributionOut)
    def host_distribution(
        metric: Literal["cpu", "memory", "disk_max"] = Query(...),
        source: list[str] | None = Query(None, description="live, synthetic, replay; default all."),
    ):
        """Utilisation distribution over the whole in-scope fleet. disk_max is the worst mount of each host."""
        sources = _csv(source, SOURCES, "source")
        name = DISTRIBUTION_METRICS[metric]
        with session_factory() as session:
            now, hosts, latest, cfg = load(session, names=[name])
        rule = cfg.rules.get(name)
        scoped = [h for h in hosts if sources is None or classify_source(h.host_id) in sources]
        buckets = [0] * len(DISTRIBUTION_BUCKETS)
        states = {"normal": 0, "warning": 0, "critical": 0, "none": 0}
        no_data = 0
        best: tuple[float, str, str] | None = None
        for h in scoped:
            fresh = _fresh(latest.get(h.host_id, []), name, h.last_seen, now)
            if metric == "disk_max":
                newest: dict[str, object] = {}
                for o in fresh:
                    mount = o.labels.get("mount", "")
                    if mount not in newest or o.observed_at > newest[mount].observed_at:
                        newest[mount] = o
                value = max((o.value for o in newest.values()), default=None)
            else:
                value = max(fresh, key=lambda o: o.observed_at).value if fresh else None
            states[_state(value, rule)] += 1
            if value is None:
                no_data += 1
                continue
            index = next((i for i, (lo, hi) in enumerate(DISTRIBUTION_BUCKETS) if lo <= value < hi), 0)
            buckets[index] += 1
            if best is None or value > best[0] or (value == best[0] and (h.hostname.casefold(), h.host_id) < (best[1].casefold(), best[2])):
                best = (value, h.hostname, h.host_id)
        return DistributionOut(
            metric=metric, sources=sorted(sources) if sources else list(SOURCES), total_in_scope=len(scoped),
            bucket_labels=["0–20", "20–40", "40–60", "60–80", "80–100"], buckets=buckets, no_data=no_data, states=states,
            max=DistributionMaxOut(value=best[0], host_id=best[2], hostname=best[1]) if best else None, complete=True,
        )

    @router.get("/hosts/rankings", response_model=RankingOut)
    def host_rankings(
        metric: Literal["system.cpu.utilization", "system.memory.utilization"] = Query(...),
        source: list[str] | None = Query(None, description="live, synthetic, replay; default all."),
        limit: int = Query(8, ge=1, le=50),
    ):
        """The top hosts by their current value of one metric, across the whole in-scope fleet (not a loaded page).
        A host counts only with a fresh sample, under the same rule as the host summary: a sample older than the staleness window
        is missing, never zero. `without_value` says how many in-scope hosts have no fresh value and are therefore not ranked."""
        sources = _csv(source, SOURCES, "source")
        with session_factory() as session:
            now, hosts, latest, cfg = load(session, names=[metric])
        scoped = [h for h in hosts if sources is None or classify_source(h.host_id) in sources]
        ranked: list[RankedHostOut] = []
        for h in scoped:
            fresh = _fresh(latest.get(h.host_id, []), metric, h.last_seen, now)
            if not fresh:
                continue
            best = max(fresh, key=lambda o: o.observed_at)
            ranked.append(RankedHostOut(
                host_id=h.host_id, hostname=h.hostname, source=classify_source(h.host_id), value=best.value,
                observed_at=best.observed_at, last_seen_age_seconds=max(0.0, (now - h.last_seen).total_seconds()),
                reporting_status=H.reporting_status(classify_source(h.host_id), h.last_seen, now, cfg),
            ))
        ranked.sort(key=lambda r: (-r.value, r.hostname.casefold(), r.host_id))
        return RankingOut(
            metric=metric, sources=sorted(sources) if sources else list(SOURCES), total_in_scope=len(scoped),
            with_value=len(ranked), without_value=len(scoped) - len(ranked), limit=limit, items=ranked[:limit], complete=True,
        )

    @router.get("/hosts/{host_id}", response_model=HostDetailOut)
    def get_host(host_id: str = Path(pattern=HOST_ID)):
        with session_factory() as session:
            now, hosts, latest, cfg = load(session, host_id)
            placement = Q.host_groups(session, default_workspace_id(session)).get(host_id)
        if not hosts:
            raise errors.host_not_found(host_id)
        return S.build_host(hosts[0], latest[host_id], now, cfg, with_metrics=True).model_copy(update=_placement(placement))

    @router.get("/hosts/{host_id}/metrics/latest", response_model=LatestMetricsOut)
    def latest_metrics(host_id: str = Path(pattern=HOST_ID)):
        with session_factory() as session:
            now, hosts, latest, cfg = load(session, host_id)
        if not hosts:
            raise errors.host_not_found(host_id)
        row = hosts[0]
        built = S.build_host(row, latest[host_id], now, cfg, with_metrics=True)
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
