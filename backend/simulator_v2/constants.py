"""Every numeric constant of M3.0 spec v4.5.1 (sections 2-4, 8, 10), defined once."""
from __future__ import annotations

SPEC_VERSION = "4.5.1"
PROTOCOL_VERSION = "4.5"
SIMULATOR_VERSION = "2.0.0"
SCHEMA_VERSION = "gt-1.0"

import os

LATENT_DT = 30                       # s
RUN_DAYS = int(os.environ.get("M31_DEV_RUN_DAYS", "14"))   # 14 for every real dataset; dev fixtures use 3 (spec 18)
RUN_LEN = RUN_DAYS * 86400           # s
N_STEPS = RUN_LEN // LATENT_DT       # 40320 intervals; grid points 0..N_STEPS
HORIZONS = (900, 1800, 3600)
L_MIN = 120
INCIDENT_MIN_DURATION = 120
COOLDOWN = 900                       # = merge_window
MERGE_WINDOW = 900
PLANNER_GUARD = 30
PLANNER_WARMUP = 7200
CONCURRENCY_LIMIT = 2
ALERT_DEDUP = 900
CADENCE_DEFAULT = 60
CADENCES = (30, 60, 120)
J_MAX = 2.0
JITTER_SIGMA = 0.5
S_MAX = 1.5
D_NORMAL = (0.05, 0.4)
REPLAY_DELAY = (0.5, 5.0)
CHUNK_DELAY = (0.05, 0.4)
REPLAY_CHUNK_MAX = 500
D_MAX = 1800
NORMAL_LAG_OBS_BOUND = 0.4
NORMAL_LAG_NOMINAL_BOUND = 3.9       # s_max + j_max + 0.4
OMIT_P = 0.002
SPIKE_P = 0.001
MAX_ATTEMPTS = 1000
MAX_REPLANS = 100
PRIMARY_CAP_PER_RUN = 15
RELAPSE_JOIN_MIN = 60
RELAPSE_JOIN_MAX = 840
RELAPSE_TRET_MAX_MIN = 14
TRET_MIN = (5, 20)                   # minutes
PRECURSOR_SIGMA = 2.0
PRECURSOR_MIN_POINTS = 3
PRECURSOR_GROUP_LEAD = 120
GENERATION_WALLCLOCK = "2026-11-12T00:00:00Z"   # fixed so the run-start constraint is deterministic

SPLITS = ("train", "val", "test_iid", "test_ood")
SPLIT_RUNS = {"train": 90, "val": 30, "test_iid": 40, "test_ood": 20}
ARCHETYPES = ("web_api", "worker_batch", "database", "queue_consumer")
OOD_NEW_ARCHETYPE = "cache_node"
OOD_SLOWDOWN = 2.5
FAMILIES = ("mem_leak", "disk_fill", "cpu_runaway", "net_flood", "pool_exhaustion", "silent_crash")
PRECURSOR_FAMILIES = FAMILIES[:5]
QUOTAS = {
    "mem_leak": {"train": 100, "val": 30, "test_iid": 45, "test_ood": 25},
    "disk_fill": {"train": 100, "val": 30, "test_iid": 45, "test_ood": 25},
    "cpu_runaway": {"train": 100, "val": 30, "test_iid": 45, "test_ood": 25},
    "net_flood": {"train": 100, "val": 30, "test_iid": 45, "test_ood": 25},
    "pool_exhaustion": {"train": 100, "val": 30, "test_iid": 45, "test_ood": 25},
    "silent_crash": {"train": 150, "val": 45, "test_iid": 68, "test_ood": 37},
}
SECONDARIES = {"train": 65, "val": 19, "test_iid": 29, "test_ood": 16}
RELAPSE_FAMILIES = ("silent_crash", "cpu_runaway", "net_flood")

SERIES = ("cpu", "mem", "disk_root", "disk_data", "rx", "tx")
SERIES_SPEC = {   # id -> (metric, labels, kind, unit)
    "cpu": ("system.cpu.utilization", {}, "gauge", "percent"),
    "mem": ("system.memory.utilization", {}, "gauge", "percent"),
    "disk_root": ("system.disk.utilization", {"mount": "/"}, "gauge", "percent"),
    "disk_data": ("system.disk.utilization", {"mount": "/data"}, "gauge", "percent"),
    "rx": ("system.network.rx_bytes", {"interface": "eth0"}, "counter", "bytes"),
    "tx": ("system.network.tx_bytes", {"interface": "eth0"}, "counter", "bytes"),
}
PEAK_HOUR = {"web_api": 14, "worker_batch": 2, "database": 12, "queue_consumer": 10, "cache_node": 13}
SERVICE_POOL = {
    "web_api": ("checkout-api", "search-api", "auth-api"),
    "worker_batch": ("report-worker", "etl-batch", "billing-batch"),
    "database": ("orders-db", "users-db", "ledger-db"),
    "queue_consumer": ("events-consumer", "mail-consumer", "audit-consumer"),
    "cache_node": ("session-cache", "page-cache", "object-cache"),
}
