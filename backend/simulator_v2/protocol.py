"""protocol.json: the frozen, machine-readable protocol values and their hash (spec section 1, 9)."""
from __future__ import annotations

import hashlib
import json

from . import constants as C
from .bounds import buffered_bounds, normal_bounds
from .footprints import CLASSES, CLASS_SIGNALS, MATRIX


def build_protocol() -> dict:
    """Frozen values only; no prose. Any change here changes protocol_hash."""
    return {
        "protocol_version": C.PROTOCOL_VERSION,
        "quotas": C.QUOTAS, "split_runs": C.SPLIT_RUNS, "secondaries": C.SECONDARIES,
        "constants": {
            "horizons_s": list(C.HORIZONS), "min_useful_lead_s": C.L_MIN, "incident_min_duration_s": C.INCIDENT_MIN_DURATION,
            "cooldown_s": C.COOLDOWN, "merge_window_s": C.MERGE_WINDOW, "planner_guard_s": C.PLANNER_GUARD,
            "planner_warmup_s": C.PLANNER_WARMUP, "concurrency_limit": C.CONCURRENCY_LIMIT, "alert_dedup_s": C.ALERT_DEDUP,
            "feature_windows_s": [300, 900, 3600], "min_window_coverage": 0.5, "min_elapsed_history_s": 3600,
            "precursor_sigma": C.PRECURSOR_SIGMA, "precursor_min_points": C.PRECURSOR_MIN_POINTS,
            "latent_dt_s": C.LATENT_DT, "run_len_s": C.RUN_LEN, "d_max_s": C.D_MAX, "j_max_s": C.J_MAX, "s_max_s": C.S_MAX,
            "normal_send_delay_s": list(C.D_NORMAL), "replay_delay_s": list(C.REPLAY_DELAY),
            "chunk_delay_s": list(C.CHUNK_DELAY), "replay_chunk_max_observations": C.REPLAY_CHUNK_MAX,
            "omit_p": C.OMIT_P, "false_alarm_budget_per_evaluable_host_day": 1,
        },
        "series_inventory": {s: list(C.SERIES_SPEC[s][:1]) + [C.SERIES_SPEC[s][1], C.SERIES_SPEC[s][2], C.SERIES_SPEC[s][3]]
                             for s in C.SERIES},
        "footprints": {"classes": {c: sorted(CLASS_SIGNALS[c]) for c in CLASSES},
                       "compatibility_matrix": {a: {b: MATRIX[a][b] for b in CLASSES} for a in CLASSES}},
        "false_alarm": {"version": "fa-2", "partition": "tick_anchored_decision_quantised",
                        "interval": "[run_start + j*delta, min(run_start + (j+1)*delta, run_end))",
                        "decision_time": "run_start + decision_offset + j*delta"},
        "buffered_membership": "true_sample_time",
        "sampling_origin": "run_start",
        "availability_bounds_s": {
            "normal": dict(zip(("lag_vs_obs_ts", "lag_vs_nominal"), normal_bounds())),
            "buffered_formula": {"lag_vs_obs_ts": "D_max + delta_r_max + 0.4*(c_max-1)",
                                 "lag_vs_nominal": "D_max + j_max + s_max + delta_r_max + 0.4*(c_max-1)"},
            "buffered": dict(zip(("lag_vs_obs_ts", "lag_vs_nominal"), buffered_bounds(1)))},
        "counter_reset_policy": {"sources": ["mem_leak_oom_host_panic_at_impact_end",
                                             "silent_crash_kernel_panic_at_impact_end",
                                             "planned_reboot_at_gap_end"],
                                 "series": ["rx", "tx"], "right_continuous": True},
        "oracle_mask": {"start": "onset", "end": "max(cooldown_end, max_member_conflict_end)",
                        "interval": "left_closed_right_open", "cooldown_s": C.COOLDOWN,
                        "members": "incident_faults_primary_and_relapse"},
        "label_precedence_version": "match-1",
        "precursor_criterion": {"sigma": C.PRECURSOR_SIGMA, "min_consecutive_grid_points": C.PRECURSOR_MIN_POINTS,
                                "group_lead_s": C.PRECURSOR_GROUP_LEAD},
    }


def canonical_json(d: dict) -> bytes:
    return json.dumps(d, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def protocol_hash(d: dict) -> str:
    return hashlib.sha256(canonical_json(d)).hexdigest()
