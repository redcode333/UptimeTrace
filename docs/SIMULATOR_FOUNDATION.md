# Simulation foundation: what the simulator supports, and what it does not

Status: foundation verified on one run and on the existing 180-run dataset. No dataset was regenerated and no model was
trained. This describes a **controlled synthetic generator**. Its runs are defined by the simulator, not measured on real
infrastructure, and nothing here says how a real customer system behaves.

## 1. Simulator used

The generator is `backend/simulator_v2` (version 2.0.0, spec 4.5.1). It is the one the frozen ML protocol and the existing
180-run dataset use, so it is the only simulator extended here. `backend/simulator` (the v1 scenarios: `normal_baseline`,
`cpu_saturation`, `memory_pressure`, `disk_pressure`, `network_spike`, `gradual_degradation`) produces telemetry without
ground-truth fault records and is not used for ML.

Reused as they are: the run allocator (`alloc.py`), the baseline and workload model (`baseline.py`), the six fault families
(`faults/families.py`), the planner and incident merge (`planner.py`, `merge.py`), telemetry rendering and the independent
audit (`ticks.py`, `signals.py`, `audit.py`), the schema validator (`validate.py`), and the writer (`writer.py`).

## 2. Scenario coverage

| Requested category | Fault family | Status |
|---|---|---|
| CPU saturation | `cpu_runaway` | supported; PRECURSOR |
| Memory pressure | `mem_leak` | supported; PRECURSOR |
| Disk capacity exhaustion | `disk_fill` | supported; PRECURSOR |
| Network latency and packet loss | none | **not supported.** `net_flood` models throughput saturation of the NIC, not latency or loss |
| Disk I/O degradation | none | **not supported** |
| Service degradation | `pool_exhaustion` | partly: connection-pool saturation with CPU, memory and network effects |
| Cascading failures | none | **not supported**: concurrency between faults exists, but no propagation between hosts |
| Abrupt outage without precursors | `silent_crash` | supported; SILENT (`process_crash`, `dependency_outage`, `kernel_panic`) |

Adding the three unsupported mechanisms needs their definitions in the simulator specification. Only sections 13 to 16 of
spec 4.5.1 are in this repository (`SIMULATOR_SPEC_V4_5_1_SECTIONS_13_16.md`), so their mechanisms cannot be implemented
faithfully from here. This is a blocker that needs the full specification or an agreed definition.

Normal operation is included: a weekly and deploy-shaped baseline, benign episodes (planned reboots, transient blips), and
telemetry gaps. Multiple hosts are supported; each run is one host.

## 3. Run identity, seeds and timestamps

* **Run ID:** `r<index>` (run index in the allocation); incidents `<run>-i<n>`, faults `fault_id`.
* **Seed:** one master seed per dataset. Each run draws from `RunStreams(master_seed, run_index)`, so a run's randomness
  does not depend on the other runs or on the worker count.
* **Dataset ID:** `uptimetrace-m31-seed<seed>-<protocol hash prefix>`.
* **Time convention:** every timestamp is an RFC 3339 UTC string with microseconds, derived from the run's start epoch and the
  latent grid (30 s). Telemetry observations, fault events, incidents and gaps use the same function (`ts_of`).
* **Sampling:** latent grid 30 s; telemetry cadence 60 s in the primary dataset (30, 60 and 120 s views exist for the dev fixture).

## 4. Ground-truth format

Each dataset folder contains, next to the raw telemetry:

| File | Content |
|---|---|
| `manifest.json` | dataset and simulator versions, `code_hash`, master seed, protocol hash, configuration, accounting, validation counts, allocation, rejection statistics, resource report |
| `file_index.json` | every data file with its size and SHA-256; the manifest's content hash (without the wall-clock `resources` block) |
| `runs.jsonl` | one record per run: host, service, archetype, split, start and end timestamps, profile |
| `faults.jsonl` | one record per injected fault: family, variant, `planned_outcome`, `outcome`, injection start (`start`), conflict window, precursor start and lead, impact start and end, resolution time, severity, parameters |
| `incidents.jsonl` | merged incidents with group (PRECURSOR, ABRUPT, SILENT) and silent reason |
| `benign_episodes.jsonl`, `gaps.jsonl`, `view_gaps.jsonl` | normal periods and missing telemetry, explicitly |
| `observations/run=<id>.parquet` | raw telemetry rows only (no truth columns; a test enforces this) |
| `protocol.json`, `view.json` | the protocol and the view configuration |

Raw telemetry, ground truth and derived features and labels are kept in separate folders (`datasets/`, `features_full/`,
`labels_full/`). The features and labels are never written into the telemetry folder.

## 5. Failed injections

A fault attempt that does not produce its planned outcome is **retried**, not recorded as a success. The simulated outcome
must equal the planned one before a fault is written (`draw_until_outcome`), and every recorded fault satisfies
`outcome == planned_outcome` (a test checks this on every record). Attempts are limited to 1,000 per fault and replans to 100
per run; the rejection reasons are counted in the manifest.

**Gap:** when the attempt limit is exhausted, `GenerationAborted` propagates and stops the whole dataset generation. The run is
not written as a recorded failed or inconclusive run. That is loud, not silent, so no false detection example is produced,
but the requested record does not exist. Recording it needs a schema decision, because it changes the protocol's counts.

## 6. Overwrite protection

`generate_dataset` refuses to write into a folder that already holds files. A folder with a previous dataset (a manifest) is
replaced only with `overwrite=True` (CLI `--overwrite`), and the whole folder is then cleared so no stale file survives. A
non-empty folder that is not a dataset is never cleared. Tests: `tests/m31/test_foundation_runs.py::TestOverwriteGuard`.

## 7. Verification performed

| Check | Where | Result |
|---|---|---|
| Existing simulator and audit suite (`tests/m31`) | `python -m pytest tests/m31` | see the run log in the report |
| Foundation tests (overwrite, index, reproducibility, timestamps, outcomes) | `tests/m31/test_foundation_runs.py` | 11 tests |
| Same config reproduces identical files; 1 vs 2 workers identical | `test_determinism_and_resources.py` | passes |
| Different seed changes the telemetry | `test_foundation_runs.py::TestReproducibility` | passes |
| Labels and telemetry are not derived from truth at render time | `test_dev_fixture_and_sidecars.py` | passes |
| Labels, features and models (M3.2 to M3.4) | `tests/m32`, `tests/m33`, `tests/m34` | 50 passed |

Not run in this task: PostgreSQL-backed tests, Docker, end-to-end scripts, the full 180-run regeneration, and the full
dataset audit (about five minutes; it reads the existing artifacts).

## 8. Dataset compatibility with the ML protocol

The protocol targets 180 runs, 14 days per run, 60 s sampling and a 30-minute horizon. The configuration supports these
values (`RUN_DAYS = 14`, `N_STEPS = 40320` at 30 s, telemetry cadence 60 s, `HORIZONS = (900, 1800, 3600)`), and the existing
artifacts match them: 180 runs (90 / 30 / 40 / 20), 21,645,047 observation rows, 1,300 merged incidents, and labels for all
three horizons. The existing dataset audit (`docs/SYNTHETIC_DATASET_AUDIT.md`) verified these counts against the files.

## 9. Blockers before a new dataset

1. **Scenario gaps:** disk I/O degradation, network latency and packet loss, and cascading failures need specified mechanisms
   (section 2).
2. **Failed-injection record:** a recorded inconclusive run needs a schema decision (section 5).
3. **Existing dataset provenance:** `code_hash` in the existing manifest does not match the current source (recorded in the
   dataset audit as unresolved). The existing folder has no `file_index.json`; adding one needs a decision, because it
   changes a frozen folder.
4. **Frozen protocol:** the protocol and splits must not change without approval, so any new scenario needs a new protocol
   version rather than a silent extension.
