"""The protocol facts and thresholds the validator checks against. Values that come from the protocol are read from its file where the
dataset provides them; the constants here are the spec's stated intent, and each one is named where it is used."""
from __future__ import annotations

VERSION = "1.0.0"

SPLITS = ("train", "val", "test_iid", "test_ood")
EVALUATION_SPLITS = ("test_iid", "test_ood")
GROUPS = ("PRECURSOR", "ABRUPT", "SILENT")
FAMILIES = ("cpu_runaway", "mem_leak", "disk_fill", "net_flood", "pool_exhaustion", "silent_crash")
SILENT_FAMILIES = ("silent_crash",)              # the only family whose incidents are SILENT by construction
SERIES = ("cpu", "mem", "disk_root", "disk_data", "rx", "tx")
METRIC_NAMES = {
    "system.cpu.utilization": "cpu",
    "system.memory.utilization": "mem",
    "system.disk.utilization": "disk",
    "system.network.rx_bytes": "rx",
    "system.network.tx_bytes": "tx",
}

RUN_LEN_S = 1209600              # 14 days (protocol constants.run_len_s)
CADENCE_S = 60                   # telemetry cadence (view.json, protocol)
DECISION_OFFSET_S = 5.0          # first decision at run_start + 5 s (labels manifest decision_offset_s)
DECISION_DELTA_S = 60            # one decision per minute (labels manifest delta_s)
DECISIONS_PER_RUN = RUN_LEN_S // DECISION_DELTA_S        # 20160
HORIZONS = (900, 1800, 3600)     # protocol constants.horizons_s
PRIMARY_HORIZON_S = 1800         # the 30-minute outage-prediction horizon
PRECURSOR_MIN_LEAD_S = 120       # protocol precursor_criterion.group_lead_s
MIN_HISTORY_S = 3600             # protocol constants.min_elapsed_history_s; decisions before this have no features

# Thresholds (hard failures unless stated)
MIN_GROUP_EXAMPLES_EVAL = 20     # each outcome category needs at least this many incidents in each evaluation split
MIN_FAMILY_GROUP_EXAMPLES = 1    # every family that can produce a category must produce at least this many in the evaluation split
GAP_EXPLAIN_TOLERANCE_S = 120    # a telemetry gap longer than this must sit inside a recorded gap window (± this tolerance)
LONG_GAP_S = 180                 # gaps at least this long are checked against the recorded gap windows
CADENCE_TOLERANCE_S = 5          # median interval must be within this of CADENCE_S
LEAKAGE_SAMPLE_PER_RUN = 25      # decisions recomputed from raw telemetry per sampled run
LEAKAGE_RUNS = ("train", "test_iid", "test_ood")   # one sampled run per split is enough to expose future information
FEATURE_TOLERANCE = 1e-3         # float32 features compared at this absolute tolerance
