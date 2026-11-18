"""Six fault families: mechanism, impact, recovery, outcome (spec sections 4.2-4.4).

Each `sim_*` function performs ONE attempt and returns a FaultSim (whose `.outcome` is what the simulation
actually produced) or None when an attempt precondition fails. `draw_until_outcome` repeats attempts.
"""
from __future__ import annotations

import numpy as np

from .. import constants as C
from ..baseline import logu
from ..footprints import footprint_class
from .common import (SPM, FaultSim, draw_recovery_steps, draw_tret_steps, first_at_least, first_run,
                     peak_index, ramp_weights, steps_ceil)

N = C.N_STEPS
MECH = {"mem_leak": "memory", "disk_fill": "faulted mount", "cpu_runaway": "cpu", "net_flood": "network",
        "pool_exhaustion": "hidden connection pool"}


class _RedrawStart:
    """Returned by sim_mem_leak when theta - M0 < 5: the spec says to redraw the START (not the other parameters)."""
    outcome = None

    def __repr__(self):
        return "REDRAW_START"


REDRAW_START = _RedrawStart()


class Ctx:
    """Per-run context a fault simulation needs."""

    def __init__(self, profile, base, slow: float = 1.0):
        self.p = profile
        self.base = base
        self.slow = slow


def _rank_logu(x, lo, hi):
    return float((np.log(x) - np.log(lo)) / (np.log(hi) - np.log(lo)))


def _phi(z):
    from math import erf, sqrt
    return 0.5 * (1 + erf(z / sqrt(2)))


def _rank_recovery(r_steps, median_min):
    return _phi((np.log(max(r_steps / SPM, 1e-6)) - np.log(median_min)) / 0.6)


def _blank(n):
    return np.ones(n), np.zeros(n)


# ------------------------------------------------------------------------------------------------ mem_leak
def sim_mem_leak(ctx: Ctx, start: int, planned: str, rng, tret_max_min=C.TRET_MIN[1], force_step=False):
    p, b = ctx.p, ctx.base
    D = float(logu(rng, 15, 360)) * ctx.slow
    theta = float(rng.uniform(93, 99.5))
    M0 = float(b.mem[start])
    if theta - M0 < 5:
        return REDRAW_START                         # spec 4.2: redraw start if theta - M0 < 5 points
    kappa = (theta - M0) / D
    lam = b.lam[start:]
    L = kappa * 0.5 * np.concatenate([[0.0], np.cumsum(lam)[:-1]])
    mem_nf = b.mem[start:] + L
    params = {"D_min": D, "theta": theta, "M0": M0, "kappa": kappa}
    if planned == "incident":
        j = first_at_least(mem_nf, theta)
        if j is None:
            return None
        delay = int(np.ceil(float(np.clip(rng.exponential(1.0), 0, 5)) * SPM - 1e-9))
        k_imp = j + delay
        r_m = float(rng.uniform(0.3, 0.6))
        mus = {s: float(rng.uniform(0.1, 0.4)) for s in ("cpu", "rx", "tx")}
        panic = bool(rng.random() < 0.15)
        R = draw_recovery_steps(rng, 6, 2, 60)
        tret = draw_tret_steps(rng, tret_max_min)
        k_end = k_imp + R
        k_conf = k_end + tret
        if start + k_conf > N or k_conf + 1 > L.size:
            return None
        n = k_conf + 1
        w = np.zeros(n)
        w[k_imp:k_end + 1] = 1.0
        kk = np.arange(k_end + 1, k_conf + 1)
        w[kk] = 1.0 - (kk - k_end) / tret
        Lw = np.zeros(n)
        Lw[:k_imp + 1] = L[:k_imp + 1]
        mem_mul = np.ones(n)
        mem_add = Lw.copy()
        after = np.arange(n) > k_imp
        mem_mul[after] = 1.0 - w[after]
        mem_add[after] = r_m * p.mem_base * w[after]
        eff = {"mem": (mem_mul, mem_add)}
        for s, mu in mus.items():
            eff[s] = (1.0 + (mu - 1.0) * w, np.zeros(n))
        fc = footprint_class("mem_leak", "incident", panic=panic)
        params.update({"r_m": r_m, "mu": mus, "panic": panic, "R_steps": R, "oom_delay_steps": delay})
        return FaultSim("mem_leak", "oom_panic" if panic else "oom", "incident", start, start + k_imp, start + k_end,
                        None, start + k_conf, None, fc, MECH["mem_leak"], eff, {"mem": Lw}, params,
                        _rank_logu(D / ctx.slow, 15, 360), start + k_end if panic else None,
                        (start + k_imp, start + k_end) if panic else None, tret)
    # twin
    peak = float(rng.uniform(82, theta - 2))
    if M0 >= peak:
        return None
    j = first_at_least(mem_nf, peak)
    if j is None:
        return None
    t_flush = steps_ceil(float(rng.uniform(2, 10)))
    tret = draw_tret_steps(rng, tret_max_min)
    k_res = j + t_flush
    k_conf = k_res + tret
    if start + k_conf > N or k_conf + 1 > L.size:
        return None
    n = k_conf + 1
    Lw = np.zeros(n)
    Lw[:j + 1] = L[:j + 1]
    kk = np.arange(j + 1, k_res + 1)
    Lw[kk] = L[j] * (1.0 - (kk - j) / t_flush)
    outcome = "incident" if np.any(b.mem[start:start + n] + Lw >= theta) else "twin"
    params.update({"peak": peak, "T_flush_steps": t_flush})
    one, zero = _blank(n)
    return FaultSim("mem_leak", "flush", outcome, start, None, None, start + k_res, start + k_conf,
                    start + peak_index(Lw), "F1", MECH["mem_leak"], {"mem": (one, Lw.copy())}, {"mem": Lw}, params,
                    _rank_logu(D / ctx.slow, 15, 360), None, None, tret)


# ------------------------------------------------------------------------------------------------ disk_fill
def sim_disk_fill(ctx: Ctx, start: int, planned: str, rng, tret_max_min=C.TRET_MIN[1], force_step=False):
    p, b = ctx.p, ctx.base
    D = float(logu(rng, 30, 600)) * ctx.slow
    mount = "/" if rng.random() < 0.6 else "/data"
    sig = "disk_root" if mount == "/" else "disk_data"
    variant = ("steady", "stepwise", "load_coupled")[int(rng.integers(0, 3))]
    r = float(rng.uniform(0.5, 5))
    arr = b.disk_root if mount == "/" else b.disk_data
    U0 = float(arr[start])
    target = 100 - r
    if U0 >= target:
        return REDRAW_START                         # start-dependent precondition (U0 < 100 - r), same handling as mem_leak
    w0 = (target - U0) / D                          # points / min
    Lw_full = N - start + 1
    if variant == "steady":
        rate = np.full(Lw_full, w0 * 0.5)
    elif variant == "load_coupled":
        rate = w0 * 0.5 * b.lam[start:]
    else:
        D_steps = int(np.ceil(D * SPM))
        segs, tot = [], 0
        while tot < D_steps:
            ln = steps_ceil(float(np.clip(rng.exponential(15), 2, 60)))
            ln = min(ln, D_steps - tot)
            segs.append((ln, float(rng.uniform(0.2, 1.8))))
            tot += ln
        wsum = sum(l * m for l, m in segs)
        norm = D_steps / wsum
        mult = np.ones(Lw_full)
        pos = 0
        for ln, m in segs:
            mult[pos:pos + ln] = m * norm
            pos += ln
        rate = w0 * 0.5 * mult
    W = np.concatenate([[0.0], np.cumsum(rate)[:-1]])
    usage = arr[start:] + W
    params = {"D_min": D, "mount": mount, "reserved_r": r, "variant": variant, "U0": U0, "w0": w0}
    tret = draw_tret_steps(rng, tret_max_min)
    if planned == "incident":
        j = first_at_least(usage, target)
        if j is None:
            return None
        R = draw_recovery_steps(rng, 20, 5, 180)
        k_end = j + R
        k_conf = k_end + tret
        if start + k_conf > N or k_conf + 1 > W.size:
            return None
        n = k_conf + 1
        w = ramp_weights(n, j, k_end, tret)
        Ww = np.zeros(n)
        Ww[:k_end + 1] = W[:k_end + 1]
        kk = np.arange(k_end + 1, n)
        Ww[kk] = W[k_end] * (1.0 - (kk - k_end) / tret)
        mus = {"cpu": float(rng.uniform(0.7, 0.95)), "tx": float(rng.uniform(0.7, 0.95))}
        eff = {sig: (np.ones(n), Ww.copy())}
        for s, mu in mus.items():
            eff[s] = (1.0 + (mu - 1.0) * w, np.zeros(n))
        params.update({"mu": mus, "R_steps": R})
        return FaultSim("disk_fill", variant, "incident", start, start + j, start + k_end, None, start + k_conf, None,
                        footprint_class("disk_fill", "incident", mount=mount), MECH["disk_fill"], eff, {sig: Ww},
                        params, _rank_logu(D / ctx.slow, 30, 600), None, None, tret, mount=mount)
    u_c = float(rng.uniform(80, target - 2))
    if U0 >= u_c:
        return None
    j_tr = first_at_least(usage, u_c)
    if j_tr is None:
        return None
    lag = int(np.ceil(float(np.clip(rng.exponential(3), 0, 30)) * SPM - 1e-9))
    drop = steps_ceil(float(rng.uniform(1, 5)))
    frac = float(rng.uniform(0, 0.3))
    k_c = j_tr + lag
    k_res = k_c + drop
    k_conf = k_res + tret
    if start + k_conf > N or k_conf + 1 > W.size:
        return None
    n = k_conf + 1
    Ww = np.zeros(n)
    Ww[:k_c + 1] = W[:k_c + 1]
    Wf = W[k_c] * frac
    kk = np.arange(k_c + 1, k_res + 1)
    Ww[kk] = W[k_c] + (Wf - W[k_c]) * (kk - k_c) / drop
    kk = np.arange(k_res + 1, n)
    Ww[kk] = Wf * (1.0 - (kk - k_res) / tret)
    outcome = "incident" if np.any(arr[start:start + n] + Ww >= target) else "twin"
    params.update({"u_c": u_c, "lag_steps": lag, "drop_steps": drop, "residual_frac": frac})
    return FaultSim("disk_fill", variant, outcome, start, None, None, start + k_res, start + k_conf,
                    start + peak_index(Ww[:k_res + 1]), footprint_class("disk_fill", "twin", mount=mount),
                    MECH["disk_fill"], {sig: (np.ones(n), Ww.copy())}, {sig: Ww}, params,
                    _rank_logu(D / ctx.slow, 30, 600), None, None, tret, mount=mount)


# ------------------------------------------------------------------------------------------------ cpu_runaway
def sim_cpu_runaway(ctx: Ctx, start: int, planned: str, rng, tret_max_min=C.TRET_MIN[1], force_step=False):
    b = ctx.base
    step = True if force_step else bool(rng.random() < 0.4)
    ramp_in = steps_ceil(float(rng.uniform(10, 60)) / 60.0)
    T_r = steps_ceil(float(rng.uniform(2, 60)) * ctx.slow)
    A = float(rng.uniform(0.25, 1.6))
    spare = 100.0 - float(b.cpu.mean())
    A_abs = A * spare
    rho_crit = float(rng.uniform(0.97, 1.10))
    ramp_steps = ramp_in if step else T_r
    Lw_full = N - start + 1
    k = np.arange(Lw_full)
    c_base = A_abs * np.minimum(1.0, k / ramp_steps)
    params = {"variant": "step" if step else "ramp", "A": A, "A_abs": A_abs, "rho_crit": rho_crit,
              "ramp_steps": ramp_steps}
    tret = draw_tret_steps(rng, tret_max_min)
    if planned == "incident":
        rho = (b.cpu[start:] + c_base) / 100.0
        j = first_run(rho >= rho_crit, 5)            # >= 120 s sustained (5 grid points)
        if j is None:
            return None
        R = draw_recovery_steps(rng, 12, 3, 90)
        k_end = j + R
        k_conf = k_end + tret
        if start + k_conf > N:
            return None
        n = k_conf + 1
        w = ramp_weights(n, j, k_end, tret)
        c = np.zeros(n)
        c[:k_end + 1] = c_base[:k_end + 1]
        kk = np.arange(k_end + 1, n)
        c[kk] = c_base[k_end] * (1.0 - (kk - k_end) / tret)
        mu = float(rng.uniform(0.3, 0.7))
        mul_tx = 1.0 + (mu - 1.0) * w
        fac = np.ones(n)
        params.update({"mu_tx": mu, "R_steps": R})
        return FaultSim("cpu_runaway", params["variant"], "incident", start, start + j, start + k_end, None,
                        start + k_conf, None, "F5", MECH["cpu_runaway"],
                        {"cpu": (fac, c.copy()), "tx": (mul_tx, np.zeros(n))}, {"cpu": c}, params,
                        float(_rank_logu(max(A, 1e-3), 0.25, 1.6)), None, None, tret)
    hold = steps_ceil(float(np.clip(rng.exponential(30), 5, 240)))
    down = steps_ceil(float(rng.uniform(1, 10)))
    k_hold_end = ramp_steps + hold
    k_res = k_hold_end + down
    k_conf = k_res + tret
    if start + k_conf > N:
        return None
    n = k_conf + 1
    c = np.zeros(n)
    c[:k_hold_end + 1] = c_base[:k_hold_end + 1]
    kk = np.arange(k_hold_end + 1, k_res + 1)
    c[kk] = A_abs * (1.0 - (kk - k_hold_end) / down)
    rho = (b.cpu[start:start + n] + c) / 100.0
    outcome = "incident" if first_run(rho >= rho_crit, 5) is not None else "twin"
    params.update({"hold_steps": hold, "down_steps": down})
    return FaultSim("cpu_runaway", params["variant"], outcome, start, None, None, start + k_res, start + k_conf,
                    start + peak_index(c), "F4", MECH["cpu_runaway"], {"cpu": (np.ones(n), c.copy())}, {"cpu": c},
                    params, float(_rank_logu(max(A, 1e-3), 0.25, 1.6)), None, None, tret)


# ------------------------------------------------------------------------------------------------ net_flood
def sim_net_flood(ctx: Ctx, start: int, planned: str, rng, tret_max_min=C.TRET_MIN[1], force_step=False):
    p, b = ctx.p, ctx.base
    flash = False if force_step else bool(rng.random() < 0.7)
    m_p = float(logu(rng, 1.5, 40))
    rise = steps_ceil(float(rng.uniform(2, 30)) * ctx.slow) if flash else steps_ceil(float(rng.uniform(10, 60)) / 60.0)
    hold = steps_ceil(float(np.clip(rng.exponential(25), 5, 180)))
    decay = steps_ceil(float(rng.uniform(5, 60)) * ctx.slow)
    lo = max(0, start - 2880)
    base_peak = float(b.rx[lo:start + 1].max())
    U_cap = min(base_peak * float(logu(rng, 3, 25)), 0.95 * p.nic_bps)
    phi_crit = float(rng.uniform(0.85, 1.0))
    thr = phi_crit * U_cap
    rho_resp = float(rng.uniform(0.1, 0.6))
    a_net = float(rng.uniform(2, 15))
    ln_peak = np.log(m_p)
    tret = draw_tret_steps(rng, tret_max_min)
    n_nat = rise + hold + decay + 1
    k = np.arange(n_nat)
    lnm = np.where(k <= rise, ln_peak * k / rise,
                   np.where(k <= rise + hold, ln_peak, ln_peak * np.clip(1 - (k - rise - hold) / decay, 0, 1)))
    params = {"shape": "flash" if flash else "step", "m_p": m_p, "U_cap": U_cap, "phi_crit": phi_crit,
              "rise_steps": rise, "hold_steps": hold, "decay_steps": decay, "rho_resp": rho_resp, "a_net": a_net}
    sev = _rank_logu(m_p, 1.5, 40)
    if start + n_nat > N:
        return None
    rate = np.minimum(b.rx[start:start + n_nat] * np.exp(lnm), p.nic_bps)
    mask = rate >= thr
    if planned == "incident":
        j = first_run(mask, 5)
        if j is None:
            return None
        after = np.nonzero(~mask[j:])[0]
        if after.size == 0:
            return None
        k_end = j + int(after[0])
        k_conf = k_end + tret
        if start + k_conf > N:
            return None
        n = k_conf + 1
        ln = np.zeros(n)
        ln[:k_end + 1] = lnm[:k_end + 1]
        kk = np.arange(k_end + 1, n)
        ln[kk] = lnm[k_end] * (1.0 - (kk - k_end) / tret)
        w = ramp_weights(n, j, k_end, tret)
        mu_tx = float(rng.uniform(0.5, 0.9))
        m = np.exp(ln)
        m_tx = 1.0 + (m - 1.0) * rho_resp
        eff = {"rx": (m.copy(), np.zeros(n)), "tx": (m_tx * (1.0 + (mu_tx - 1.0) * w), np.zeros(n)),
               "cpu": (np.ones(n), a_net * ln / ln_peak)}
        params["mu_tx"] = mu_tx
        return FaultSim("net_flood", params["shape"], "incident", start, start + j, start + k_end, None, start + k_conf,
                        None, "F8", MECH["net_flood"], eff, {"rx": ln, "tx": np.log(m_tx)}, params, sev, None, None, tret)
    k_res = rise + hold + decay
    k_conf = k_res + tret
    if start + k_conf > N:
        return None
    n = k_conf + 1
    ln = np.zeros(n)
    ln[:n_nat] = lnm
    m = np.exp(ln)
    m_tx = 1.0 + (m - 1.0) * rho_resp
    outcome = "incident" if first_run(mask, 5) is not None else "twin"
    eff = {"rx": (m.copy(), np.zeros(n)), "tx": (m_tx, np.zeros(n)), "cpu": (np.ones(n), a_net * ln / ln_peak)}
    return FaultSim("net_flood", params["shape"], outcome, start, None, None, start + k_res, start + k_conf,
                    start + peak_index(ln), "F8", MECH["net_flood"], eff, {"rx": ln, "tx": np.log(m_tx)}, params, sev,
                    None, None, tret)


# ------------------------------------------------------------------------------------------------ pool_exhaustion
def sim_pool_exhaustion(ctx: Ctx, start: int, planned: str, rng, tret_max_min=C.TRET_MIN[1], force_step=False):
    b = ctx.base
    D = float(logu(rng, 60, 480)) * ctx.slow
    a_cpu, a_mem = float(rng.uniform(8, 25)), float(rng.uniform(8, 25))
    c_net = float(rng.choice([-1, 1]) * rng.uniform(0.1, 0.5))
    Lw_full = N - start + 1
    P_raw = np.concatenate([[0.0], np.cumsum(b.lam[start:] * 0.5 / D)[:-1]])
    params = {"D_min": D, "a_cpu": a_cpu, "a_mem": a_mem, "c_net": c_net}
    tret = draw_tret_steps(rng, tret_max_min)
    sev = _rank_logu(D / ctx.slow, 60, 480)

    def build(P, n, w=None):
        eff = {"cpu": ((np.ones(n) if w is None else 1.0 + (mus["cpu"] - 1.0) * w), a_cpu * P * (1.0 if w is None else (1.0 + (mus["cpu"] - 1.0) * w))),
               "mem": (np.ones(n), a_mem * P)}
        for s in ("rx", "tx"):
            mu_f = np.ones(n) if w is None else 1.0 + (mus[s] - 1.0) * w
            eff[s] = (np.exp(c_net * P) * mu_f, np.zeros(n))
        return eff

    delta_of = lambda P: {"mem": a_mem * P, "cpu": a_cpu * P, "rx": c_net * P, "tx": c_net * P}
    if planned == "incident":
        j = first_at_least(P_raw, 1.0)
        if j is None:
            return None
        R = draw_recovery_steps(rng, 8, 2, 60)
        k_end = j + R
        k_conf = k_end + tret
        if start + k_conf > N:
            return None
        n = k_conf + 1
        mus = {s: float(rng.uniform(0.1, 0.4)) for s in ("cpu", "rx", "tx")}
        P = np.zeros(n)
        P[:j + 1] = np.minimum(P_raw[:j + 1], 1.0)
        P[j:k_end + 1] = 1.0
        kk = np.arange(k_end + 1, n)
        P[kk] = 1.0 - (kk - k_end) / tret
        w = ramp_weights(n, j, k_end, tret)
        params.update({"mu": mus, "R_steps": R})
        return FaultSim("pool_exhaustion", "pool", "incident", start, start + j, start + k_end, None, start + k_conf,
                        None, "F9", MECH["pool_exhaustion"], build(P, n, w), delta_of(P), params, sev, None, None, tret)
    p_stab = float(rng.uniform(0.5, 0.97))
    j = first_at_least(P_raw, p_stab)
    if j is None:
        return None
    hold = steps_ceil(float(np.clip(rng.exponential(30), 10, 240)))
    down = steps_ceil(float(rng.uniform(5, 30)))
    k_hold_end = j + hold
    k_res = k_hold_end + down
    k_conf = k_res + tret
    if start + k_conf > N:
        return None
    n = k_conf + 1
    P = np.zeros(n)
    P[:j + 1] = np.minimum(P_raw[:j + 1], p_stab)
    P[j:k_hold_end + 1] = p_stab
    kk = np.arange(k_hold_end + 1, k_res + 1)
    P[kk] = p_stab * (1.0 - (kk - k_hold_end) / down)
    params.update({"P_stab": p_stab, "hold_steps": hold, "down_steps": down})
    outcome = "incident" if np.any(P >= 1.0) else "twin"
    return FaultSim("pool_exhaustion", "pool", outcome, start, None, None, start + k_res, start + k_conf,
                    start + peak_index(P), "F9", MECH["pool_exhaustion"], build(P, n), delta_of(P), params, sev,
                    None, None, tret)


# ------------------------------------------------------------------------------------------------ silent_crash
def sim_silent_crash(ctx: Ctx, start: int, planned: str, rng, tret_max_min=C.TRET_MIN[1], force_step=False):
    p = ctx.p
    u = rng.random()
    sub = "process_crash" if u < 0.60 else ("dependency_outage" if u < 0.85 else "kernel_panic")
    tret = draw_tret_steps(rng, tret_max_min)
    params = {"subtype": sub}
    if sub == "process_crash":
        R = draw_recovery_steps(rng, 3, 2, 30)
        med = 3
    elif sub == "dependency_outage":
        R = draw_recovery_steps(rng, 15, 3, 120)
        med = 15
    else:
        R = steps_ceil(float(rng.uniform(4, 20)))
        med = 12
    k_end = R
    k_conf = k_end + tret
    if start + k_conf > N:
        return None
    n = k_conf + 1
    w = ramp_weights(n, 0, k_end, tret)
    eff, reset, gap = {}, None, None
    if sub == "process_crash":
        mus = {s: float(rng.uniform(0.05, 0.30)) for s in ("cpu", "rx", "tx")}
        r_m = float(rng.uniform(0.3, 0.6))
        for s, mu in mus.items():
            eff[s] = (1.0 + (mu - 1.0) * w, np.zeros(n))
        eff["mem"] = (1.0 - w, r_m * p.mem_base * w)
        params.update({"mu": mus, "r_m": r_m})
    elif sub == "dependency_outage":
        mus = {"rx": float(rng.uniform(0.05, 0.30)), "tx": float(rng.uniform(0.05, 0.30)), "cpu": float(rng.uniform(0.3, 0.7))}
        for s, mu in mus.items():
            eff[s] = (1.0 + (mu - 1.0) * w, np.zeros(n))
        params["mu"] = mus
    else:
        reset, gap = start + k_end, (start, start + k_end)
    fc = footprint_class("silent_crash", "incident", subtype=sub)
    sev = float(_rank_recovery(R, med)) if sub != "kernel_panic" else float((R / SPM - 4) / 16)
    params["R_steps"] = R
    return FaultSim("silent_crash", sub, "incident", start, start, start + k_end, None, start + k_conf, None, fc,
                    "host process" if sub == "process_crash" else ("dependency" if sub == "dependency_outage" else "host"),
                    eff, {}, params, sev, reset, gap, tret, subtype=sub)


SIM = {"mem_leak": sim_mem_leak, "disk_fill": sim_disk_fill, "cpu_runaway": sim_cpu_runaway,
       "net_flood": sim_net_flood, "pool_exhaustion": sim_pool_exhaustion, "silent_crash": sim_silent_crash}


class GenerationAborted(RuntimeError):
    pass


def draw_until_outcome(family: str, ctx: Ctx, start: int, planned: str, make_rng, max_attempts=C.MAX_ATTEMPTS,
                       tret_max_min=C.TRET_MIN[1], force_step=False, accept=None):
    """Repeat attempts until the SIMULATED outcome equals `planned` (and `accept(fault)` holds).

    make_rng(attempt) -> Generator. Returns (fault, attempts_used). Raises GenerationAborted after max_attempts.
    """
    for attempt in range(1, max_attempts + 1):
        f = SIM[family](ctx, start, planned, make_rng(attempt), tret_max_min, force_step)
        if f is None or f.outcome != planned:
            continue
        if accept is not None and not accept(f):
            continue
        return f, attempt
    raise GenerationAborted(f"{family} {planned} at step {start}: {max_attempts} attempts exhausted")
