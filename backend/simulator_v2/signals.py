"""Latent signals on the 30 s grid: fault composition, noise, exact integer counters, sampling (spec 3.4, 8.1)."""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from . import constants as C

N = C.N_STEPS
DT = C.LATENT_DT
PCT = ("cpu", "mem", "disk_root", "disk_data")


def ar1(rng, n, phi, std):
    """Zero-mean AR(1), stationary std `std`, exact discretisation."""
    z = rng.standard_normal(n)
    out = np.empty(n)
    out[0] = std * z[0]
    inn = std * np.sqrt(1.0 - phi * phi)
    prev = out[0]
    for i in range(1, n):
        prev = phi * prev + inn * z[i]
        out[i] = prev
    return out


@dataclass
class Latent:
    pct: dict                       # series -> emitted-before-clip value at each grid point (len N+1)
    cnt_grid: dict                  # rx/tx -> int64 counter at each grid point (len N+1)
    inc: dict                       # rx/tx -> int64 bytes added in interval [t_i, t_i+1) (len N)
    resets: dict                    # rx/tx -> list of (grid_index, base)
    reset_idx: np.ndarray           # sorted grid indices of resets (same for rx and tx)
    nf: dict = field(default_factory=dict)   # noise-free levels (for tests / diagnostics)

    def sample_pct(self, series: str, u: np.ndarray) -> np.ndarray:
        """Linear interpolation of y at times u (s from run_start), clipped to [0,100], rounded to 2 decimals."""
        y = self.pct[series]
        x = u / DT
        i = np.minimum(np.floor(x).astype(np.int64), N - 1)
        f = x - i
        v = y[i] + (y[i + 1] - y[i]) * f
        return np.round(np.clip(v, 0.0, 100.0), 2)

    def sample_counter(self, series: str, u: np.ndarray) -> np.ndarray:
        """floor(C(u)) for the right-continuous piecewise-linear counter; int64."""
        x = u / DT
        i = np.minimum(np.floor(x).astype(np.int64), N - 1)
        frac = (u - i * DT) / DT
        return self.cnt_grid[series][i] + np.floor(self.inc[series][i] * frac).astype(np.int64)


def build_latent(profile, base, plan, episodes, streams) -> Latent:
    lv = {"cpu": base.cpu.copy(), "mem": base.mem.copy(), "disk_root": base.disk_root.copy(),
          "disk_data": base.disk_data.copy(), "rx": base.rx.copy(), "tx": base.tx.copy()}
    for pf in sorted(plan.faults, key=lambda f: f.sim.start_i):
        pf.sim.apply_to(lv)
    # planned reboots: heap-drift memory resets at the end of the gap
    prev = 0.0
    for (s, e) in sorted(episodes["reboots"], key=lambda x: x[1]):
        lv["mem"][e:] -= base.drift[e] - prev
        prev = float(base.drift[e])
    # transient blips
    for (s, e), prm in zip(episodes["blips"], episodes["blip_params"]):
        mu = prm["mu"]
        lv["cpu"][s:e + 1] *= mu["cpu"]
        lv["mem"][s:e + 1] *= mu["mem"]
        lv["rx"][s:e + 1] *= mu["net"]
        lv["tx"][s:e + 1] *= mu["net"]
    rn = streams.get("noise")
    om = streams.get("omissions")
    pct, rate = {}, {}
    for s in PCT:
        phi = profile.noise_phi60[s] ** 0.5
        z = ar1(rn, N + 1, phi, profile.sigma_marg[s])
        spike = rn.random(N + 1) < C.SPIKE_P
        mag = rn.uniform(3, 6, N + 1) * profile.sigma_marg[s] * rn.choice([-1.0, 1.0], N + 1)
        z = z + np.where(spike, mag, 0.0)
        scale = 1.0 + 0.5 * lv[s] / 100.0
        pct[s] = lv[s] + scale * z
    for s in ("rx", "tx"):
        phi = profile.noise_phi60[s] ** 0.5
        z = ar1(rn, N + 1, phi, profile.sigma_marg[s])
        rate[s] = np.minimum(np.maximum(lv[s], 0.0) * np.exp(z), profile.nic_bps)
    # counters: integer byte increments (exact), resets at the end of reboot / panic gaps (right-continuous)
    reset_idx = sorted({e for (_, e) in episodes["reboots"]} | {pf.sim.reset_i for pf in plan.faults if pf.sim.reset_i is not None})
    reset_idx = np.array(reset_idx, dtype=np.int64)
    cnt_grid, inc, resets = {}, {}, {}
    for s in ("rx", "tx"):
        d = np.rint(rate[s][:N] * DT).astype(np.int64)
        inc[s] = d
        c0 = profile.c0_rx if s == "rx" else profile.c0_tx
        grid = np.empty(N + 1, dtype=np.int64)
        bounds = [0] + [int(g) for g in reset_idx] + [N + 1]
        bases = [(0, int(c0))]
        grid_seg_base = [int(c0)] + [int(rn.integers(0, 10 ** 6 + 1)) for _ in reset_idx]
        for k in range(len(bounds) - 1):
            a, b = bounds[k], bounds[k + 1]
            seg_inc = d[a:min(b, N)] if a < N else np.zeros(0, dtype=np.int64)
            cs = np.concatenate([[0], np.cumsum(seg_inc)])[:b - a]
            grid[a:b] = grid_seg_base[k] + cs
        assert grid.max() < 2 ** 53, "counter total exceeds 2^53"
        cnt_grid[s] = grid
        resets[s] = [(int(g), grid_seg_base[k + 1]) for k, g in enumerate(reset_idx)]
    return Latent(pct=pct, cnt_grid=cnt_grid, inc=inc, resets=resets, reset_idx=reset_idx, nf=lv)
