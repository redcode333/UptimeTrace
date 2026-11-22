"""SQL for the read API. All of it runs against `telemetry_envelopes` as it exists: no new tables, no migration.

Strategy (the stored shape is one row per envelope, metrics inside `payload->'metrics'` JSONB):

  * Host discovery uses the existing index ix_telemetry_envelopes_host_ts (host_id, envelope_timestamp). A recursive CTE
    "skip scan" walks the distinct host_ids (one index probe per host), then two LATERAL probes read each host's first and
    last envelope (one index probe each). Cost is O(hosts x log rows), not O(rows).
  * Latest metrics read only each host's newest envelopes (at most 1000, and none older than an hour before the host's
    last one). ORDER BY .. LIMIT inside the LATERAL probe is what makes the planner use the index per host (without it the
    subquery is flattened into a sequential scan: 300 ms vs 3 ms at 295k rows). They are unnested the metrics
    with jsonb_array_elements and keep the two newest samples per (name, labels) series (two, so counters can yield a rate).
  * Time series bound the envelope scan with the index (host_id + envelope_timestamp range) and aggregate in SQL, so the
    result is one row per series and bucket, never the raw telemetry. Observation time (metric.timestamp), not envelope send
    time, places a point; the envelope window only narrows what is scanned (see LATE_ARRIVAL_GRACE).
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.monitoring.rates import MAX_RATE_GAP

LATEST_LOOKBACK = timedelta(hours=1)
# Contract: observation.timestamp <= envelope.timestamp + 5 s, so an envelope sent before (start - 5 s) cannot hold an
# observation inside the window.
SKEW = timedelta(seconds=5)
# Observations can arrive late (an agent flushes a buffer after an outage). We scan envelopes sent up to this long after
# `end`; a sample delivered later than that is not found by a series query. Documented limitation.
LATE_ARRIVAL_GRACE = timedelta(hours=24)


@dataclass(frozen=True)
class HostRow:
    host_id: str
    hostname: str
    agent_id: str
    service: str | None
    first_seen: datetime
    last_seen: datetime


@dataclass(frozen=True)
class SampleRow:
    host_id: str
    name: str
    kind: str
    unit: str
    labels: dict
    value: float
    observed_at: datetime
    previous: tuple[datetime, float] | None  # the next-older sample of the same series, for counter rates


@dataclass(frozen=True)
class SeriesBucket:
    labels: dict
    bucket: int
    avg: float
    min: float
    max: float
    last: float
    samples: int


_HOSTS = """
WITH RECURSIVE hosts AS (
    (SELECT host_id FROM telemetry_envelopes ORDER BY host_id LIMIT 1)
    UNION ALL
    SELECT (SELECT host_id FROM telemetry_envelopes WHERE host_id > h.host_id ORDER BY host_id LIMIT 1)
    FROM hosts h WHERE h.host_id IS NOT NULL
)
SELECT h.host_id, f.first_seen, l.agent_id, l.hostname, l.service, l.last_seen
FROM hosts h
CROSS JOIN LATERAL (
    SELECT envelope_timestamp AS first_seen FROM telemetry_envelopes
    WHERE host_id = h.host_id ORDER BY envelope_timestamp ASC, id ASC LIMIT 1) f
CROSS JOIN LATERAL (
    SELECT agent_id, hostname, service, envelope_timestamp AS last_seen FROM telemetry_envelopes
    WHERE host_id = h.host_id ORDER BY envelope_timestamp DESC, id DESC LIMIT 1) l
WHERE h.host_id IS NOT NULL {only}
ORDER BY h.host_id
"""


def fetch_hosts(session: Session, host_id: str | None = None) -> list[HostRow]:
    if host_id is None:
        rows = session.execute(text(_HOSTS.format(only=""))).all()
    else:
        # One host: skip the walk, probe the index directly.
        rows = session.execute(text(_HOSTS.format(only="AND h.host_id = :host")), {"host": host_id}).all()
    return [HostRow(r.host_id, r.hostname, r.agent_id, r.service, r.first_seen, r.last_seen) for r in rows]


_GROUPS = """
SELECT h.host_id, e.id AS env_id, e.name AS env_name, r.id AS reg_id, r.name AS reg_name, c.id AS cl_id, c.name AS cl_name
FROM hosts h
LEFT JOIN clusters c ON c.id = h.cluster_id AND c.workspace_id = h.workspace_id
LEFT JOIN regions r ON r.id = c.region_id AND r.workspace_id = h.workspace_id
LEFT JOIN environments e ON e.id = r.environment_id AND e.workspace_id = h.workspace_id
WHERE h.workspace_id = :wid
"""

_SERVICES = """
SELECT h.host_id, s.id AS svc_id, s.name AS svc_name
FROM service_hosts sh
JOIN hosts h ON h.id = sh.host_pk AND h.workspace_id = sh.workspace_id
JOIN services s ON s.id = sh.service_id AND s.workspace_id = sh.workspace_id
WHERE sh.workspace_id = :wid
ORDER BY s.name, s.id
"""


def host_groups(session: Session, wid) -> dict[str, dict]:
    """Registry placement of each host: environment, region, cluster and services, keyed by host_id. Hosts absent here have no placement."""
    out: dict[str, dict] = {}
    for r in session.execute(text(_GROUPS), {"wid": wid}).all():
        out[r.host_id] = {
            "environment": {"id": str(r.env_id), "name": r.env_name} if r.env_id else None,
            "region": {"id": str(r.reg_id), "name": r.reg_name} if r.reg_id else None,
            "cluster": {"id": str(r.cl_id), "name": r.cl_name} if r.cl_id else None,
            "services": [],
        }
    for r in session.execute(text(_SERVICES), {"wid": wid}).all():
        entry = out.setdefault(r.host_id, {"environment": None, "region": None, "cluster": None, "services": []})
        entry["services"].append({"id": str(r.svc_id), "name": r.svc_name})
    return out


_LATEST = """
SELECT host_id, name, kind, unit, labels, ts, value, rn FROM (
    SELECT l.host_id,
           m->>'name' AS name, m->>'kind' AS kind, m->>'unit' AS unit,
           COALESCE(m->'labels', '{}'::jsonb) AS labels,
           (m->>'timestamp')::timestamptz AS ts,
           m->'value' AS value,
           row_number() OVER (
               PARTITION BY l.host_id, m->>'name', COALESCE(m->'labels', '{}'::jsonb)
               ORDER BY (m->>'timestamp')::timestamptz DESC) AS rn
    FROM unnest(CAST(:hosts AS text[]), CAST(:lasts AS timestamptz[])) AS l(host_id, last_seen)
    CROSS JOIN LATERAL (
        SELECT payload FROM telemetry_envelopes e
        WHERE e.host_id = l.host_id AND e.envelope_timestamp > l.last_seen - make_interval(secs => :lookback)
        ORDER BY e.envelope_timestamp DESC LIMIT 1000) e
    CROSS JOIN LATERAL jsonb_array_elements(e.payload->'metrics') m
    WHERE (CAST(:names AS text[]) IS NULL OR m->>'name' = ANY(CAST(:names AS text[])))
) s
WHERE rn <= 2
ORDER BY host_id, name, labels::text, rn
"""


def fetch_latest(session: Session, hosts: list[HostRow], names: list[str] | None = None) -> dict[str, list[SampleRow]]:
    """Newest sample per (name, labels) for each host, within LATEST_LOOKBACK of that host's last envelope."""
    out: dict[str, list[SampleRow]] = {h.host_id: [] for h in hosts}
    if not hosts:
        return out
    rows = session.execute(
        text(_LATEST),
        {
            "hosts": [h.host_id for h in hosts],
            "lasts": [h.last_seen for h in hosts],
            "lookback": LATEST_LOOKBACK.total_seconds(),
            "names": names,
        },
    ).all()
    current: dict[tuple, dict] = {}
    for r in rows:
        key = (r.host_id, r.name, json.dumps(r.labels, sort_keys=True))
        if r.rn == 1:
            current[key] = dict(host_id=r.host_id, name=r.name, kind=r.kind, unit=r.unit, labels=r.labels,
                                value=r.value, observed_at=r.ts, previous=None)
        else:  # rn == 2: the next-older sample of the same series (rows arrive rn-ordered)
            current[key]["previous"] = (r.ts, r.value)
    for fields in current.values():
        out[fields["host_id"]].append(SampleRow(**fields))
    return out


def host_exists(session: Session, host_id: str) -> bool:
    return bool(
        session.execute(
            text("SELECT 1 FROM telemetry_envelopes WHERE host_id = :h LIMIT 1"), {"h": host_id}
        ).first()
    )


_KIND = """
SELECT m->>'kind' AS kind, m->>'unit' AS unit
FROM telemetry_envelopes e CROSS JOIN LATERAL jsonb_array_elements(e.payload->'metrics') m
WHERE e.host_id = :host AND e.envelope_timestamp >= :lo AND e.envelope_timestamp <= :hi AND m->>'name' = :name
LIMIT 1
"""


def metric_kind(session: Session, host_id: str, name: str, lo: datetime, hi: datetime) -> tuple[str, str] | None:
    row = session.execute(text(_KIND), {"host": host_id, "name": name, "lo": lo, "hi": hi}).first()
    return (row.kind, row.unit) if row else None


_GAUGE = """
WITH obs AS (
    SELECT (m->>'timestamp')::timestamptz AS ts,
           COALESCE(m->'labels', '{}'::jsonb) AS labels,
           (m->>'value')::double precision AS v
    FROM telemetry_envelopes e CROSS JOIN LATERAL jsonb_array_elements(e.payload->'metrics') m
    WHERE e.host_id = :host AND e.envelope_timestamp >= :lo AND e.envelope_timestamp <= :hi AND m->>'name' = :name
)
SELECT labels,
       floor(extract(epoch FROM (ts - CAST(:start AS timestamptz))) / :step)::int AS bucket,
       avg(v) AS avg, min(v) AS min, max(v) AS max,
       (array_agg(v ORDER BY ts DESC))[1] AS last, count(*) AS samples
FROM obs
WHERE ts >= CAST(:start AS timestamptz) AND ts < CAST(:end AS timestamptz)
GROUP BY labels, bucket
ORDER BY labels::text, bucket
"""

# Counter -> rate. lag() runs over ALL scanned samples (including ones before `start`, so the first interval inside the
# window has a predecessor); only rates whose sample time is inside [start, end) are bucketed.
_COUNTER = """
WITH obs AS (
    SELECT (m->>'timestamp')::timestamptz AS ts,
           COALESCE(m->'labels', '{}'::jsonb) AS labels,
           (m->>'value')::numeric AS v
    FROM telemetry_envelopes e CROSS JOIN LATERAL jsonb_array_elements(e.payload->'metrics') m
    WHERE e.host_id = :host AND e.envelope_timestamp >= :lo AND e.envelope_timestamp <= :hi AND m->>'name' = :name
), pairs AS (
    SELECT labels, ts, v,
           lag(v)  OVER w AS pv,
           lag(ts) OVER w AS pts
    FROM obs
    WINDOW w AS (PARTITION BY labels ORDER BY ts)
), rates AS (
    SELECT labels, ts,
           CASE
               WHEN pv IS NULL OR v < pv THEN NULL
               WHEN extract(epoch FROM (ts - pts)) <= 0 OR extract(epoch FROM (ts - pts)) > :maxgap THEN NULL
               ELSE ((v - pv) / extract(epoch FROM (ts - pts)))::double precision
           END AS rate
    FROM pairs
    WHERE ts >= CAST(:start AS timestamptz) AND ts < CAST(:end AS timestamptz)
)
SELECT labels,
       floor(extract(epoch FROM (ts - CAST(:start AS timestamptz))) / :step)::int AS bucket,
       avg(rate) AS avg, min(rate) AS min, max(rate) AS max,
       (array_agg(rate ORDER BY ts DESC))[1] AS last, count(*) AS samples
FROM rates
WHERE rate IS NOT NULL
GROUP BY labels, bucket
ORDER BY labels::text, bucket
"""


def series(
    session: Session,
    host_id: str,
    name: str,
    kind: str,
    start: datetime,
    end: datetime,
    step: int,
) -> list[SeriesBucket]:
    """One row per (label set, bucket) that has data. Buckets without data are absent; the caller makes the gaps explicit."""
    lo = start - SKEW - (MAX_RATE_GAP if kind == "counter" else timedelta(0))
    hi = end + LATE_ARRIVAL_GRACE
    params = {"host": host_id, "name": name, "lo": lo, "hi": hi, "start": start, "end": end, "step": step}
    if kind == "counter":
        params["maxgap"] = MAX_RATE_GAP.total_seconds()
        rows = session.execute(text(_COUNTER), params).all()
    else:
        rows = session.execute(text(_GAUGE), params).all()
    return [SeriesBucket(r.labels, r.bucket, r.avg, r.min, r.max, r.last, r.samples) for r in rows]
