"""Ticks, gaps, omissions, envelopes, buffered replay and the exact accounting ledger (spec 8.2-8.5, 10.1)."""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

import numpy as np

from . import constants as C

N = C.N_STEPS
DT = C.LATENT_DT
_EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)


def ts_str(us: int) -> str:
    """Integer UTC microseconds -> contract timestamp string (6 fractional digits)."""
    d = _EPOCH + timedelta(microseconds=int(us))
    return d.strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def truncated_jitter(rng, n: int) -> np.ndarray:
    """TN(0, 0.5, +-2): redraw (not clip) anything outside +-2 s."""
    j = rng.normal(0.0, C.JITTER_SIGMA, n)
    bad = np.abs(j) > C.J_MAX
    while bad.any():
        j[bad] = rng.normal(0.0, C.JITTER_SIGMA, int(bad.sum()))
        bad = np.abs(j) > C.J_MAX
    return j


def make_uuid(rng) -> str:
    hi, lo = (int(x) for x in rng.integers(0, 2 ** 63, 2, dtype=np.int64))
    v = ((hi << 64) | lo) & ((1 << 128) - 1)
    return str(uuid.UUID(int=v, version=4))


def omission_mask(profile, streams) -> np.ndarray:
    """(N+1, 6) bool, True = omitted. Defined on the latent grid so every cadence view shares it."""
    return streams.get("omissions").random((N + 1, len(C.SERIES))) < C.OMIT_P


@dataclass
class Ledger:
    ticks_nominal: int = 0
    lost_agent_down: int = 0
    lost_planned_reboot_gap: int = 0
    lost_panic_gap: int = 0
    normal_emitting: int = 0
    normal_all_omitted: int = 0
    buffered_emitting: int = 0
    buffered_all_omitted: int = 0
    partitions_total: int = 0
    partitions_with_replay: int = 0
    envelopes_normal: int = 0
    envelopes_replay: int = 0
    observations_candidate: int = 0
    observations_omitted_normal: int = 0
    observations_omitted_buffered: int = 0
    observations_normal: int = 0
    observations_buffered: int = 0
    max_lag_obs_normal: float = 0.0
    max_lag_nominal_normal: float = 0.0
    max_lag_obs_buffered: float = 0.0
    max_lag_nominal_buffered: float = 0.0
    max_chunks: int = 0

    def as_dict(self) -> dict:
        return dict(self.__dict__)


@dataclass
class RunOutput:
    """Everything produced for one run and one cadence view."""
    envelopes: list = field(default_factory=list)      # contract dicts (for validation)
    columns: dict = field(default_factory=dict)        # Parquet columns (numpy / lists)
    ledger: Ledger = field(default_factory=Ledger)
    view_gaps: list = field(default_factory=list)
    n_envelopes: int = 0
    tick_info: dict = field(default_factory=dict)      # u, n, class codes (kept for tests / dev fixtures)


def chunk_observations(obs: list, size: int = C.REPLAY_CHUNK_MAX) -> list:
    """Consecutive chunks of at most `size` observations, in order; empty input gives no chunk."""
    return [obs[i:i + size] for i in range(0, len(obs), size)]


def account(code: np.ndarray, part: np.ndarray, omitted: np.ndarray, n_partitions: int) -> dict:
    """Pure tick accounting (spec 10.1) from class codes, partition ids and the (K, 6) omission matrix."""
    produced = (code == NORMAL) | (code == BUFFERED)
    all_om = omitted.all(axis=1)
    om_n = omitted.sum(axis=1)
    normal, buffered = code == NORMAL, code == BUFFERED
    d = {
        "ticks_nominal": int(code.size), "lost_agent_down": int((code == LOST_AGENT).sum()),
        "lost_planned_reboot_gap": int((code == LOST_REBOOT).sum()), "lost_panic_gap": int((code == LOST_PANIC).sum()),
        "normal_emitting": int((normal & ~all_om).sum()), "normal_all_omitted": int((normal & all_om).sum()),
        "buffered_emitting": int((buffered & ~all_om).sum()), "buffered_all_omitted": int((buffered & all_om).sum()),
        "observations_candidate": int(6 * produced.sum()),
        "observations_omitted_normal": int(om_n[normal].sum()), "observations_omitted_buffered": int(om_n[buffered].sum()),
    }
    d["observations_normal"] = 6 * (d["normal_emitting"] + d["normal_all_omitted"]) - d["observations_omitted_normal"]
    d["observations_buffered"] = 6 * (d["buffered_emitting"] + d["buffered_all_omitted"]) - d["observations_omitted_buffered"]
    d["partitions_total"] = n_partitions
    with_replay = chunks = 0
    for pi in range(n_partitions):
        obs_p = int((6 - om_n[buffered & (part == pi)]).sum())
        if obs_p:
            with_replay += 1
            chunks += -(-obs_p // C.REPLAY_CHUNK_MAX)
    d["partitions_with_replay"] = with_replay
    d["envelopes_normal"] = d["normal_emitting"]
    d["envelopes_replay"] = chunks
    return d


# tick class codes
LOST_AGENT, LOST_REBOOT, LOST_PANIC, NORMAL, BUFFERED = 1, 2, 3, 4, 6


def _classify(u, gaps_by_type):
    """gaps_by_type: dict type -> list of (a_s, b_s). Returns class code array and partition index (or -1)."""
    code = np.full(u.shape, NORMAL, dtype=np.int8)
    part = np.full(u.shape, -1, dtype=np.int64)
    for typ, c in (("agent_down", LOST_AGENT), ("planned_reboot_gap", LOST_REBOOT), ("panic_gap", LOST_PANIC)):
        for a, b in gaps_by_type.get(typ, []):
            code[(u >= a) & (u < b)] = c
    for pi, (a, b) in enumerate(gaps_by_type.get("net_partition_buffered", [])):
        m = (u >= a) & (u < b)
        assert not np.any(m & (code != NORMAL)), "partition overlaps a loss interval"
        code[m] = BUFFERED
        part[m] = pi
    return code, part


def build_run_output(profile, latent, gaps, cadence: int, jitter_on: bool, streams, omit: np.ndarray,
                     service_field: bool = True, overrides: dict | None = None, partition_ids: list | None = None) -> RunOutput:
    """gaps: dict type -> list of (start_i, end_i) grid intervals. Builds ticks, envelopes, Parquet columns."""
    ov = overrides or {}
    out = RunOutput()
    led = out.ledger
    K = C.RUN_LEN // cadence
    n_nom = np.arange(K, dtype=np.int64) * cadence                                  # origin = run_start
    jr = streams.fresh("jitter", cadence)
    j = truncated_jitter(jr, K) if jitter_on else np.zeros(K)
    u = n_nom + j
    u[0] = max(u[0], 0.0)                                                           # u_0 >= run_start
    gaps_s = {t: [(a * DT, b * DT) for a, b in v] for t, v in gaps.items()}
    code, part = _classify(u, gaps_s)
    led.ticks_nominal = K
    led.lost_agent_down = int((code == LOST_AGENT).sum())
    led.lost_planned_reboot_gap = int((code == LOST_REBOOT).sum())
    led.lost_panic_gap = int((code == LOST_PANIC).sum())
    produced = np.nonzero((code == NORMAL) | (code == BUFFERED))[0]
    # omission mask: nearest grid point, ties to the earlier one
    gi = np.clip(np.ceil(u / DT - 0.5).astype(np.int64), 0, N)
    om = omit[gi]                                                                   # (K, 6)
    # values at true sample time
    vals = {}
    for s in C.SERIES:
        vals[s] = latent.sample_pct(s, u) if s in ("cpu", "mem", "disk_root", "disk_data") else latent.sample_counter(s, u)
    skew = ov.get("skew_s", profile.skew_s)
    start_us = profile.run_start_epoch * 1_000_000
    T_us = start_us + np.rint((u + skew) * 1e6).astype(np.int64)
    ids_rng = streams.fresh("ids", cadence)
    delay_rng = streams.fresh("gaps", 1000 + cadence)
    metas = [(C.SERIES_SPEC[s][0], C.SERIES_SPEC[s][1], C.SERIES_SPEC[s][2], C.SERIES_SPEC[s][3]) for s in C.SERIES]
    labels_json = [__import__("json").dumps(m[1], sort_keys=True, separators=(",", ":")) for m in metas]

    env_records = []        # (env_us, seq, kind, envelope_id, [(tick_k, series_idx)...], extra)
    seq = 0
    # --- normal ticks
    normal_ks = [int(k) for k in produced if code[k] == NORMAL]
    d_norm = delay_rng.uniform(*C.D_NORMAL, size=len(normal_ks)) if normal_ks else np.zeros(0)
    for idx, k in enumerate(normal_ks):
        keep = [si for si in range(6) if not om[k, si]]
        led.observations_candidate += 6
        if not keep:
            led.normal_all_omitted += 1
            led.observations_omitted_normal += 6
            continue
        led.normal_emitting += 1
        led.observations_omitted_normal += 6 - len(keep)
        led.observations_normal += len(keep)
        e_us = int(T_us[k] + round(float(d_norm[idx]) * 1e6))
        env_records.append((e_us, seq, "normal", make_uuid(ids_rng), [(k, si) for si in keep], None))
        seq += 1
        led.max_lag_obs_normal = max(led.max_lag_obs_normal, (e_us - T_us[k]) / 1e6)
        led.max_lag_nominal_normal = max(led.max_lag_nominal_normal, (e_us - (start_us + n_nom[k] * 1_000_000)) / 1e6)
    led.envelopes_normal = led.normal_emitting
    # --- buffered partitions
    for pi, (a_i, b_i) in enumerate(gaps.get("net_partition_buffered", [])):
        led.partitions_total += 1
        ks = [int(k) for k in produced if code[k] == BUFFERED and part[k] == pi]
        obs = []
        for k in ks:
            led.observations_candidate += 6
            keep = [si for si in range(6) if not om[k, si]]
            if not keep:
                led.buffered_all_omitted += 1
                led.observations_omitted_buffered += 6
                continue
            led.buffered_emitting += 1
            led.observations_omitted_buffered += 6 - len(keep)
            led.observations_buffered += len(keep)
            obs.extend((k, si) for si in keep)
        chunks = chunk_observations(obs)
        rec_ids = []
        if chunks:
            led.partitions_with_replay += 1
            led.max_chunks = max(led.max_chunks, len(chunks))
            b_s = b_i * DT
            e_us = start_us + int(round((b_s + skew + (ov["replay_delay"] if "replay_delay" in ov else float(delay_rng.uniform(*C.REPLAY_DELAY)))) * 1e6))
            for ci, ch in enumerate(chunks):
                if ci > 0:
                    e_us += int(round(float(delay_rng.uniform(*C.CHUNK_DELAY)) * 1e6))
                eid = make_uuid(ids_rng)
                rec_ids.append(eid)
                env_records.append((e_us, seq, "replay", eid, ch, pi))
                seq += 1
                for (k, si) in ch:
                    led.max_lag_obs_buffered = max(led.max_lag_obs_buffered, (e_us - T_us[k]) / 1e6)
                    led.max_lag_nominal_buffered = max(led.max_lag_nominal_buffered,
                                                       (e_us - (start_us + n_nom[k] * 1_000_000)) / 1e6)
        led.envelopes_replay += len(chunks)
        out.view_gaps.append({"gap_id": partition_ids[pi] if partition_ids else f"{profile.run_id}-part{pi + 1:02d}", "replay_envelope_ids": rec_ids,
                              "ticks_buffered": len(ks), "chunks": len(chunks)})
    # --- order, build contract dicts and Parquet columns
    env_records.sort(key=lambda r: (r[0], r[1]))
    n_obs = sum(len(r[4]) for r in env_records)
    col_val = np.empty(n_obs, dtype=np.float64)
    col_obs = np.empty(n_obs, dtype=np.int64)
    col_env = np.empty(n_obs, dtype=np.int64)
    col_metric, col_labels, col_kind, col_unit, col_eid = [], [], [], [], []
    tick_ts_cache: dict[int, str] = {}
    pos = 0
    for (e_us, _sq, kind, eid, items, _extra) in env_records:
        metrics = []
        e_ts = ts_str(e_us)
        for (k, si) in items:
            if k not in tick_ts_cache:
                tick_ts_cache[k] = ts_str(int(T_us[k]))
            name, labels, mkind, unit = metas[si]
            sname = C.SERIES[si]
            v = vals[sname][k]
            v = int(v) if mkind == "counter" else float(v)
            m = {"name": name, "kind": mkind, "value": v, "unit": unit, "timestamp": tick_ts_cache[k]}
            if labels:
                m["labels"] = labels
            metrics.append(m)
            col_val[pos] = v
            col_obs[pos] = T_us[k]
            col_env[pos] = e_us
            col_metric.append(name); col_labels.append(labels_json[si]); col_kind.append(mkind); col_unit.append(unit)
            col_eid.append(eid)
            pos += 1
        env = {"schema_version": "1.0", "envelope_id": eid, "agent_id": profile.agent_id, "host_id": profile.host_id,
               "hostname": profile.hostname, "timestamp": e_ts, "metrics": metrics}
        if profile.service and service_field:
            env["service"] = profile.service
        out.envelopes.append(env)
    out.n_envelopes = len(env_records)
    out.columns = {"host_id": profile.host_id, "agent_id": profile.agent_id, "value": col_val, "obs_ts": col_obs,
                   "env_ts": col_env, "metric": col_metric, "labels": col_labels, "kind": col_kind, "unit": col_unit,
                   "envelope_id": col_eid}
    out.tick_info = {"u": u, "n": n_nom, "code": code, "partition": part, "T_us": T_us, "omit": om}
    return out
