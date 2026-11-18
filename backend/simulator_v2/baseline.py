"""Host profile, workload and noise-free baseline (spec sections 3.1-3.3, 3.5 baseline benign processes)."""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from . import constants as C

N = C.N_STEPS                      # last grid index; arrays have N + 1 points
DT = C.LATENT_DT
EPOCH_2024 = 1704067200            # 2024-01-01T00:00:00Z


def logu(rng, lo, hi, size=None):
    return np.exp(rng.uniform(np.log(lo), np.log(hi), size))


def grid_ceil(seconds: float) -> int:
    """Seconds -> grid steps, snapped up."""
    return int(np.ceil(seconds / DT - 1e-9))


def grid_round(seconds: float) -> int:
    return int(np.floor(seconds / DT + 0.5))


@dataclass
class HostProfile:
    run_id: str
    split: str
    archetype: str
    ood_slow: bool
    run_start_epoch: int
    tz_offset_h: float
    cpu_base: float
    mem_base: float
    disk_root_base: float
    disk_data_base: float
    s_cpu: float
    s_mem: float
    nic_bps: float
    rx_base: float
    tx_base: float
    c0_rx: int
    c0_tx: int
    skew_s: float
    service: str | None
    a1: float
    a2: float
    phi1: float
    phi2: float
    weekend_factor: float
    sigma_ou: float
    burst_rate_per_day: float
    heap_drift: float
    t_gc_min: float
    g_gc: float
    root_growth: float
    data_growth: float
    noise_phi60: dict
    noise_sigma: dict          # eps std (percent series) or sigma_r (rate series)
    sigma_marg: dict           # noise floor per series (percent points / log units)
    host_id: str = ""
    agent_id: str = ""
    hostname: str = ""

    def to_dict(self) -> dict:
        return {k: (v if not isinstance(v, np.generic) else v.item()) for k, v in self.__dict__.items()}


def draw_profile(streams, run_index: int, split: str, archetype: str, ood_slow: bool) -> HostProfile:
    r = streams.get("profile")
    run_id = f"r{run_index:04d}"
    day = int(r.integers(0, 731))
    start = EPOCH_2024 + day * 86400 + int(r.uniform(0, 86400))
    start -= start % DT
    nic = float(r.choice([1.25e8, 1.25e9]))
    u_net = float(logu(r, 0.005, 0.10))
    rx_base = u_net * nic
    tx_base = float(r.uniform(0.3, 3.0)) * rx_base
    service = None
    pool = C.SERVICE_POOL[archetype]
    pick = pool[int(r.integers(0, 3))]
    if r.random() >= 0.2:
        service = pick
    peak = C.PEAK_HOUR[archetype]
    weekend = float(r.uniform(1.0, 1.6)) if archetype == "worker_batch" else float(r.uniform(0.4, 1.0))
    phi60, sig, marg = {}, {}, {}
    for s in C.SERIES:
        phi = float(r.uniform(0.6, 0.95))
        phi60[s] = phi
        if s in ("rx", "tx"):
            sig[s] = float(r.uniform(0.05, 0.30))
            marg[s] = sig[s]
        else:
            sig[s] = float(r.uniform(0.5, 3.0))
            marg[s] = sig[s] / np.sqrt(1 - phi ** 2)
    p = HostProfile(
        run_id=run_id, split=split, archetype=archetype, ood_slow=ood_slow, run_start_epoch=start,
        tz_offset_h=float(r.uniform(0, 24)), cpu_base=float(r.uniform(8, 40)), mem_base=float(r.uniform(25, 70)),
        disk_root_base=float(r.uniform(30, 75)), disk_data_base=float(r.uniform(15, 70)),
        s_cpu=float(r.uniform(0.3, 0.9)), s_mem=float(r.uniform(0.05, 0.3)), nic_bps=nic, rx_base=rx_base,
        tx_base=tx_base, c0_rx=int(r.integers(10 ** 9, 5 * 10 ** 12 + 1)), c0_tx=int(r.integers(10 ** 9, 5 * 10 ** 12 + 1)),
        skew_s=float(r.uniform(-C.S_MAX, C.S_MAX)), service=service,
        a1=float(r.uniform(0.15, 0.6)), a2=float(r.uniform(0, 0.2)),
        phi1=float((r.uniform(peak - 3, peak + 3)) % 24), phi2=float(r.uniform(0, 12)),
        weekend_factor=weekend, sigma_ou=float(r.uniform(0.03, 0.12)), burst_rate_per_day=float(r.uniform(2, 10)),
        heap_drift=float(r.uniform(0, 1.5)), t_gc_min=float(r.uniform(5, 30)), g_gc=float(r.uniform(0.02, 0.10)),
        root_growth=float(r.uniform(0.05, 1.0)), data_growth=float(r.uniform(0.1, 2.0)),
        noise_phi60=phi60, noise_sigma=sig, sigma_marg=marg,
    )
    p.host_id = f"sim-{run_id}"
    p.agent_id = f"sim-agent-{run_id}"
    p.hostname = f"{archetype.replace('_', '-')}-{run_id}.sim.local"
    return p


def build_workload(p: HostProfile, streams) -> tuple[np.ndarray, np.ndarray]:
    """lambda-tilde on the latent grid (run mean exactly 1) and its 30-minute trailing mean."""
    r = streams.get("workload")
    t = np.arange(N + 1) * DT
    t_local_h = ((p.run_start_epoch + t) / 3600.0 + p.tz_offset_h) % 24
    A = 1 + p.a1 * np.cos(2 * np.pi * (t_local_h - p.phi1) / 24) + p.a2 * np.cos(2 * np.pi * (t_local_h - p.phi2) / 12)
    local_days = np.floor((p.run_start_epoch + t + p.tz_offset_h * 3600) / 86400).astype(np.int64)
    weekday = (local_days + 3) % 7                     # 1970-01-01 was a Thursday (Mon = 0)
    W = np.where(weekday >= 5, p.weekend_factor, 1.0)
    theta = 1 / 12.0                                   # per hour
    phi = np.exp(-theta * DT / 3600.0)
    z = r.standard_normal(N + 1)
    ou = np.empty(N + 1)
    ou[0] = p.sigma_ou * z[0]
    innov = p.sigma_ou * np.sqrt(1 - phi ** 2)
    for i in range(1, N + 1):
        ou[i] = phi * ou[i - 1] + innov * z[i]
    B = np.zeros(N + 1)
    n_bursts = int(r.poisson(p.burst_rate_per_day * C.RUN_DAYS))
    for _ in range(n_bursts):
        s = int(r.integers(0, N + 1))
        h = float(np.clip(np.exp(0.3 + 0.6 * r.standard_normal()), 0.05, 2.0))
        d = float(np.clip(r.exponential(6.0), 0.5, 60.0)) * 60.0
        B[s:s + max(1, grid_ceil(d))] += h
    B = np.minimum(B, 3.0)
    raw = np.maximum(0.05, A) * W * np.exp(ou) * (1 + B)
    lam = raw / raw.mean()
    # 30-minute trailing mean (60 grid points)
    cs = np.concatenate([[0.0], np.cumsum(lam)])
    idx = np.arange(N + 1)
    lo = np.maximum(0, idx - 59)
    smooth = (cs[idx + 1] - cs[lo]) / (idx + 1 - lo)
    return lam, smooth


@dataclass
class Baseline:
    lam: np.ndarray
    lam_smooth: np.ndarray
    f_shift: np.ndarray
    cpu: np.ndarray
    mem: np.ndarray
    disk_root: np.ndarray
    disk_data: np.ndarray
    rx: np.ndarray            # modelled rate (B/s) before faults / noise
    tx: np.ndarray
    drift: np.ndarray         # heap-drift component (for reboot memory reset)
    benign: list = field(default_factory=list)

    def level(self, name: str) -> np.ndarray:
        return getattr(self, name)


def build_baseline(p: HostProfile, lam: np.ndarray, lam_smooth: np.ndarray, streams) -> Baseline:
    r = streams.get("benign")
    t = np.arange(N + 1) * DT
    days = t / 86400.0
    benign: list[dict] = []
    ep = 0

    def add(kind, start_i, end_i, **params):
        nonlocal ep
        ep += 1
        benign.append({"episode_id": f"{p.run_id}-b{ep:03d}", "run_id": p.run_id, "type": kind,
                       "start_i": int(start_i), "end_i": int(end_i), "params": params})

    # deploy shifts
    f = np.ones(N + 1)
    n_dep = int(r.poisson(0.15 * C.RUN_DAYS))
    shifts = sorted((int(r.integers(0, N + 1)), float(r.choice([-1, 1]) * r.uniform(0.05, 0.15))) for _ in range(n_dep))
    mult = 1.0
    for s_i, d in shifts:
        mult *= (1 + d)
        f[s_i:] = np.clip(mult, 0.5, 1.6)
        add("deploy_shift", s_i, s_i, delta=d)
    cpu = np.clip(p.cpu_base * f * ((1 - p.s_cpu) + p.s_cpu * lam), 0, 95)
    mem = p.mem_base * f * ((1 - p.s_mem) + p.s_mem * lam_smooth)
    drift = p.heap_drift * days
    mem = mem + drift
    t_gc = p.t_gc_min * 60.0
    mem = mem + p.g_gc * p.mem_base * ((t / t_gc) % 1.0 - 0.5)
    # log rotation on "/": sawtooth that drops by `drop` every period, net trend preserved (implementation reading)
    period_h = float(r.uniform(6, 24))
    drop = float(r.uniform(1, 6))
    period_s = period_h * 3600.0
    droot = p.disk_root_base + p.root_growth * days + drop * ((t / period_s) % 1.0)
    ddata = p.disk_data_base + p.data_growth * days
    add("log_rotation", 0, N, period_h=period_h, drop_points=drop)
    rx = p.rx_base * lam * f
    tx = p.tx_base * lam * f
    # cron batch jobs
    for d in range(C.RUN_DAYS):
        for _ in range(int(r.integers(1, 7))):
            s_i = d * 2880 + int(r.integers(0, 2880))
            dur = float(r.uniform(5, 40)) * 60
            e_i = min(N + 1, s_i + max(1, grid_ceil(dur)))
            pts = float(r.uniform(10, 40))
            cpu[s_i:e_i] += pts
            add("cron_batch", s_i, e_i, cpu_points=pts)
    # nightly backup (local start U(1,4) h)
    first_local_day = int((p.run_start_epoch + p.tz_offset_h * 3600) // 86400)
    for d in range(first_local_day, first_local_day + C.RUN_DAYS + 2):
        start_h = float(r.uniform(1, 4))
        dur = float(r.uniform(15, 90)) * 60
        txm = float(r.uniform(1.5, 4))
        pts = float(r.uniform(3, 12))
        rel = d * 86400 + start_h * 3600 - p.tz_offset_h * 3600 - p.run_start_epoch
        s_i = int(rel // DT)
        if rel < 0 or s_i > N:
            continue
        e_i = min(N + 1, s_i + max(1, grid_ceil(dur)))
        tx[s_i:e_i] *= txm
        cpu[s_i:e_i] += pts
        add("nightly_backup", s_i, e_i, tx_mult=txm, cpu_points=pts)
    rx = np.minimum(rx, p.nic_bps)
    tx = np.minimum(tx, p.nic_bps)
    return Baseline(lam=lam, lam_smooth=lam_smooth, f_shift=f, cpu=cpu, mem=mem, disk_root=droot,
                    disk_data=ddata, rx=rx, tx=tx, drift=drift, benign=benign)
