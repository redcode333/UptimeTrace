# SMD audit (Server Machine Dataset), 2026-11-19

Read-only. Reproduce: `python -I scripts/audit_smd.py --out docs/evidence/smd_audit.json` (`UPTIMETRACE_DATA_ROOT` set). The script parses the raw text itself and does not import the adapter, so the adapter is checked, not trusted. Two consecutive runs produced byte-identical JSON. No model was trained and no data was written to the data tree.

Archive integrity is **RECORDED BUT NOT INDEPENDENTLY VERIFIABLE** (archive not retained). Extracted raw-file integrity is verified (see `smd/raw/PROVENANCE_ADDENDUM_2026-11-19.json`).

## 1. Raw structure
* Upstream: `NetManAIOps/OmniAnomaly` @ `7fb0e0ac…`, folder `ServerMachineDataset/` with `train/`, `test/`, `test_label/`, `interpretation_label/`, each 28 files, plus a dataset-specific MIT `LICENSE`. 113 files there, 115 in the SMD raw tree with the root `LICENSE` and `README.md`.
* 28 machines in 3 groups: `machine-1-*` (8), `machine-2-*` (9), `machine-3-*` (11). Every machine has all four files.
* Text hygiene, checked on every train/test/test_label file: comma-separated, no header, no BOM, no CRLF, no blank lines, constant field count, no letters. **0 issues.**

## 2. Size
| | rows |
|---|---|
| train | 708,405 |
| test | 708,420 |
| total | 1,416,825 (matches the 56 normalised files) |

38 dimensions on every machine. Per-machine totals fall in two clusters: 19 machines near 47.4k rows, 9 near 57.4k (range 47,374 to 57,486). Train and test have equal length for 13 of 28 machines and differ by 1 row on the others.

## 3. Features (38 dimensions, `f00`..`f37`)
* **No names, units or meanings in the files.** The upstream README does not define them either. Any semantic reading (CPU, memory, ...) would be a guess and is not made.
* All values lie in **[0, 1]** (global min 0.0, max 1.0 in both splits).
* **dim 7 is exactly 0.0 in every row of every machine** in train and test. It carries no information. It is never named in an interpretation label.
* 8 dimensions (4, 7, 16, 17, 26, 28, 36, 37) are constant in train on at least 17 of 28 machines. Over all 1,064 machine-dimension pairs, 192 are constant in train, and **17 of those vary in test**, so a detector sees a never-before-seen variation there.
* Full per-dimension table (min, max, mean, std, share of exact 0 and 1, number of low-cardinality and monotone machines, how often each dimension is named in interpretation labels) is in `docs/evidence/smd_audit.json` -> `features`.
* **Missing / malformed values: 0 NaN, 0 Inf, no unparsable token** in any train, test or test_label file. The NaN count of 0 in the manifest is correct.

## 4. Timestamps and sampling
* **No timestamps, no sampling interval in any file.** The normalised `t_index` is row order.
* The upstream README says "5-week-long". 5 weeks is 50,400 minutes, and machines hold about 47.4k or 57.4k rows (32.9 or 39.9 days at one row per minute). So "1-minute sampling" is **consistent only roughly and not for all machines**. It is an inference from a README claim and the audit does not adopt it.

## 5. Labels
* **29,444** anomalous test points of 708,420 = **4.156 %** (the upstream README states 4.16 %). Per machine: 0.42 % to 15.65 %, median 3.33 %.
* **327 segments** (maximal runs of 1), 1 to 30 per machine, median 10.5. Machine `machine-2-8` has a single segment.
* Segment length (rows): min 2, p25 4, **median 11**, p75 72, p90 239, **max 3,161**, mean 90. 126 segments last 5 rows or fewer, 67 last 100 or more. The **longest 10 % of segments hold 65.5 % of all anomalous points**, so point-level metrics are dominated by a few long events.
* No segment touches the first or last test row. Only 1 pair of segments is 10 rows or fewer apart (minimum gap 4, median 1,066).
* On average 31.1 % of a machine's anomalous points fall in the first half of test.
* Labels exist for **test only**; train is unlabelled and may contain anomalies (the benchmark assumes it is normal).
* **`interpretation_label`**: 327 lines, one per segment, format `a-b:dim,dim,...` with `a-b` = `[a, b)` 0-based (established empirically). 322 of 327 intervals are identical to a point-label segment. **5 disagree**: 3 share the start but not the end (one extends past the point-label segment), 2 start at a position that is not a segment start, and 3 machines have a different interval count. Dimensions per segment: min 1, median 4, max 34. Treat point labels as the source of truth.

## 6. Does the normalised v1.0 faithfully represent the source? **Yes.**
Checked for all 56 files, row by row against an independent parse of the raw text:

| check | result |
|---|---|
| float32 values equal float32(raw float64), exactly | 56 / 56 |
| max absolute / relative error of the float32 cast | 2.98e-08 / 5.9e-08 |
| labels equal raw `test_label` | 56 / 56 (train files have none, by design) |
| row counts, `t_index` = 0..n-1 | 0 mismatches |
| schema (`t_index` int64, `f00..f37` float32, `label` int8 on test) | 0 mismatches |
| parquet SHA-256 and row / NaN / anomaly / segment counts vs `manifest.json` | 0 mismatches |
| raw-file SHA-256 vs `raw_file_sha256` in the manifest (112 files) | all equal |

What the normalisation **does not** carry: `interpretation_label` (dropped on purpose), the machine group, and any real time axis (none exists). A first run of my check reported 56 schema errors and 0 interpretation matches; both were bugs in my check (pyarrow prints float32 as `float`, and the interval convention is 0-based) and were fixed and re-verified.

## 7. Leakage and evaluation hazards
* **Scaling used test data (strong inference).** Values are min-max scaled to [0, 1]. The train split alone spans exactly [0, 1] in only **186 of 1,064** machine-dimension pairs, while train + test together do in **889**. For 26 dimensions, all 28 machines reach exactly 1.0 only once test is included (dims 36 and 37 have a train maximum of 0.8885 and a test maximum of 1.0). That pattern fits scaling fitted on each machine's whole series. It means test-range information is embedded in the train values, mildly flattering any method that assumes train defines the scale. It cannot be undone without the original values.
* No exact duplicate rows within any train file, and no test row identical to a train row on the same machine.
* The train-to-test boundary is continuous: the step across it is 1.0 times the median step (no seam).
* Distribution shift is common: in **145 of 1,064** pairs the test mean differs from the train mean by more than one train standard deviation.
* Train may contain anomalies (unlabelled).
* `interpretation_label` is derived from the labels. Using it as an input feature would leak the target.
* Point-adjusted metrics inflate scores on long segments (see section 5) and must not be used.

## 8. Compatibility with UptimeTrace telemetry
UptimeTrace consumes named series per host: `cpu`, `mem`, `disk_root`, `disk_data` (percent gauges) and `rx`, `tx` (byte counters turned into rates), at a 60 s cadence with timestamps, gaps and incident records.

| need | SMD |
|---|---|
| named series cpu / mem / disk / network | **No.** 38 anonymous channels, meanings unpublished |
| units | **No.** scaled to [0, 1] |
| timestamps and cadence | **No** (row order only) |
| counters and resets | **No** (values already scaled) |
| outage / impact intervals | **No.** labels mean "expert-labelled anomalous point" |
| host identity and metadata | machine id only |

**Proposed feature mapping:** none onto UptimeTrace's six named series. The only defensible mapping is structural: machine -> entity, row order -> tick, `f00..f37` -> unnamed channels `ch00..ch37`, with `dim 7` flagged as constant. Because the meaning of a channel is unknown, UptimeTrace's windowed features (which depend on named series and counter semantics) cannot be computed.

## 9. Limitations
No time axis, no feature meaning, scaling fitted on the whole series, labels at point level from one source with 5 interval disagreements in the side file, train not guaranteed clean, a single company's machines over about 5 weeks, one origin (no population diversity), and archive hash not re-verifiable.

## 10. Recommendation
* **ADOPT, narrowly, as an external anomaly-detection benchmark** (per machine, fit on train, score test). It is clean, complete, correctly normalised and well-licensed.
* **REJECT for outage-risk prediction.** Do not convert anomaly labels into outage labels. Do not train the outage-risk predictor on SMD, or transfer SMD-trained models to UptimeTrace features.
* **DEFER** any cross-dataset use (SMD with PSM, KPI or NAB). Each stays isolated.

### Proposed external evaluation protocol (not run)
1. Unit of analysis is one machine. Fit on that machine's train only; never pool machines or datasets.
2. Validation = last 20 % of that machine's train (temporal). The test split is never used to choose a threshold or hyper-parameter.
3. Threshold from a pre-declared false-alert budget (alerts per 1,000 rows), picked on validation, then frozen.
4. Metrics at **event level** (a segment counts as detected if at least one alert falls inside it, plus a bounded early-lead window declared in advance) together with the alert rate, and a point-level PR-AUC. **No point-adjustment.** Report macro-average over the 28 machines and per group, with segment-length strata (<= 5, 6 to 100, > 100), because 10 % of segments hold 65 % of points.
5. Report the scaling caveat. Do not re-scale with test statistics. Drop `dim 7` from fitted models (it is constant) but keep it in the stored data.
6. Use `interpretation_label` only to score localisation, never as input; use point labels as ground truth.
7. Candidate detectors, from the existing plan: robust z-score, the rolling robust baseline, Isolation Forest, ECOD / COPOD, with explicit seeds and recorded versions (`requirements.lock`).
