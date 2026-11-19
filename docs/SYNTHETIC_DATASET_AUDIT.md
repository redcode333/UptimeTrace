# Synthetic dataset reliability audit, 2026-11-19

Dataset: `uptimetrace-m31-seed0-2723fc6789d8` (simulator_v2, spec 4.5.1). No model was trained, tuned or loaded for scoring; no dataset, label, split or model file was modified; Docker, WSL and the database were not touched.
Evidence: `docs/evidence/synthetic_dataset_audit.json`. Reproduce: `python -I scripts/audit_synthetic_dataset.py --out docs/evidence/synthetic_dataset_audit.json` (about 5 minutes, read-only, byte-identical on rerun). The script does not import the production labeler, feature code or simulator: incidents are re-derived from raw fault events, labels from the dataset's own `protocol.json` rules, and as-of features from raw observations.

Tags: **[V]** verified against code or artifacts here, **[I]** strong inference, **[U]** unresolved.

## 1. Executive verdict

**TRUSTED WITH SPECIFIC LIMITATIONS** as a controlled synthetic testbed.
**NOT TRUSTWORTHY UNTIL REMEDIATED**: the existing ML evaluation (saved models and their report). That is a separate finding from the dataset.

What the dataset can legitimately support: comparing methods on a known generative process, with correct labels, as-of features and clean run-level splits. What it cannot support: any statement about real outages, real base rates, real false-alarm rates, silent-failure detection, or generalisation to new failure mechanisms.

| Use | Verdict |
|---|---|
| 1. Anomaly-detection research | **Yes, with limits.** Fault intervals, precursor starts and twins are recorded, so detectors can be scored against injected deviations. The deviations are simulator-shaped and easier than real ones, so absolute scores will be optimistic. |
| 2. Synthetic 30-minute outage-prediction research | **Yes, with limits.** Labels, horizon and as-of rule are correct. The evaluation must be done on the full splits, at incident level, with the 30 % of incidents that are unpredictable by design reported rather than dropped. |
| 3. Comparing ML models | **Not yet.** Only after the protocol in section 9 (full splits, several seeds, incident-level metrics, budget-based thresholds). A ranking on this generator may not transfer. |
| 4. Claims about real-world outage prediction | **No.** An "outage" here is a threshold crossing defined by the simulator; no real incident data was used or compared. |

## 2. Verified inventory

* **[V]** All documented counts are exact: 180 runs, split 90 / 30 / 40 / 20, 2,429 faults, 1,300 merged incidents, 12,794 benign episodes, 1,465 telemetry gaps. 180 of 180 observation files exist (21,645,047 rows, equal to the manifest). The protocol hash matches, validation shows 0 schema, semantic and footprint failures.
* **[V]** `labels_full` (540 files = 180 runs x 3 horizons) and `features_full` (180 files, 196 features, 3,548,896 rows) exist and every file matches its manifest SHA-256.
* **[V]** `artifacts/labels` and `artifacts/features` are a **7-run development subset** (r0000, r0040, r0095, r0125, r0157, r0170, r0177: 2 train, 1 val, 2 test_iid, 2 test_ood). Only `labels_full` and `features_full` cover the full dataset.
* **[V]** `datasets/...seed1...` is **partial and unusable**: 108 observation files, no manifest, runs, faults or incidents. It cannot be labelled and is no replicate.
* **[V]** All 180 runs come from **one master seed (0)**. One dataset instance exists.
* **[V]** The manifest `code_hash` does not match the current `simulator_v2` source. Only `distinct.py` is newer than the dataset (12:45 against 12:16 on 2026-11-12). **[I]** `distinct.py` is a post-hoc audit helper, so the generator is probably unchanged. **[U]** This cannot be proven: the repository has a single commit and the old file is gone.
* **[V]** The saved models (`artifacts/models/.../h1800`) were trained on **2 of 90 train runs and 1 of 30 validation runs** (2.2 % and 3.3 %). The full-data models and report were never saved.

## 3. Simulator findings

**What each fault family does** (code and data agree):

| Family | Mechanism | An incident begins when |
|---|---|---|
| `mem_leak` | memory climbs at a load-coupled rate | observed memory reaches theta in [93, 99.5] % |
| `disk_fill` | steady, stepwise or load-coupled growth on `/` or `/data` | usage reaches 100 - r, r in [0.5, 5] % |
| `cpu_runaway` | step or ramp of extra CPU | utilisation / capacity stays >= rho_crit (0.97 to 1.10) for 150 s |
| `net_flood` | flash or step rx surge | rx >= 0.85 to 1.0 x a capacity cap for 150 s |
| `pool_exhaustion` | hidden pool fills, side effects on cpu/mem/net | the hidden pool reaches 1.0 |
| `silent_crash` | process crash, dependency outage or kernel panic | immediately at fault start |

1. **Is an incident just a saturation event? [V] Yes.** Observed memory 5 min before onset has median 94.2 % against the fault's own theta median 95.9 % (disk: 96.6 % against 97.5 %). The prediction task for five of six families is "will a visible, rising utilisation reach a known threshold within 30 min". That is mechanically easy for trend features and is the main reason this cannot validate real outage prediction.
2. **Faults without incidents: [V] yes.** 1,000 **twins** (200 per precursor family, one per primary): same mechanism, peak below the threshold, no incident. They are good hard negatives. `silent_crash` has no twins.
3. **Incidents without precursors: [V] yes.** Of 1,300 incidents, 899 are PRECURSOR, **101 ABRUPT** (net_flood 65, cpu_runaway 36) and **300 SILENT** (all `silent_crash`). **30.8 % are unpredictable by design**, and `silent_crash` is the largest single family (23 %). Split shares are stable (30.0 to 32.3 %).
4. **Silent failures: [V] represented only as "no warning before onset".** All 300 are `by_construction`; the `sub_noise` path never occurs. Process crashes and dependency outages are visible in telemetry only after onset. A real silent failure may never be visible at all.
5. **Timestamps: [V] consistent.** Eight checks over all 2,429 faults found 0 violations (start <= impact_start < impact_end <= conflict_end, impact >= 120 s, precursor lead equals impact_start minus precursor_start, twin has no impact, and so on). My independent merge reproduced all 1,300 incidents exactly (onset, recovery, cooldown, mask end, group, primary, member count). No incident starts inside another incident's oracle mask.
6. **Overlapping faults: [V] rare, and never incident with incident.** 155 faults (6.4 %) overlap another; every overlap involves a twin (primary+twin 61, twin+twin 22, twin+relapse 1). Concurrent real failures are not represented.
7. **Benign variety: [V] six episode types** (cron batch 3.5 per day, nightly backup, log rotation, deploy shift, planned reboot, transient blip), with the same rates in every split. **[I]** Narrow compared with production.
8. **Gaps: [V] four types, small and independent of faults.** 4.2 agent-down and 2.7 buffered partitions per run; together about 0.6 % of run time (0.57 to 0.68 % by split), plus 0.2 % random metric omission. Gap starts fall inside a fault window 9.3 % of the time against 11.2 % of run time inside any fault window, so gaps are not correlated with failures. No frozen metrics, clock jumps or partial-series loss exist.
9. **Same scenario, different telemetry: [V] parameters vary, morphology does not.** Every fault draws its own parameters (for example `mem_leak` D_min 15 to 349 min), but each family has 1 to 3 fixed shapes (`pool_exhaustion` has exactly one). **[I]** The four non-silent families are easy to recognise by shape once an episode is under way.
10. **Does the simulator leak future or identity into telemetry? [V] No path found.** Telemetry is generated from baseline + fault effects + noise; no ID, label, run position or future incident field is emitted. What does exist is selection structure: faults are placed by rejection sampling so the planned outcome is realised (for example a `mem_leak` incident needs headroom of at least 5 points at its start).

## 4. Label correctness

Definitions **[V]** (from `protocol.json`, the labeler and my re-derivation):

* A merged incident's group comes from its **primary fault**: **PRECURSOR** if the latent noise-free deviation exceeds 2 sigma for at least 3 grid points, beginning at least **120 s** before impact; **ABRUPT** if that lead is under 120 s; **SILENT** if no precursor exists (`silent_crash`).
* Decision points: one per 60 s at +5 s from run start. Positive window = a PRECURSOR onset in `(t, t+H]`. **Unscorable** = onsets in the window are only ABRUPT/SILENT. **Excluded** = `t` inside `[onset, oracle_mask_end)` of any incident, or failing the data-sufficiency rule.
* Positive / negative / unscorable / excluded at H = 1800, all runs: positive 26,621; unscorable 10,978; excluded insufficient data 36,890, excluded active incident 43,014; negative the rest. Prevalence of positives among scored rows 0.66 to 0.85 %.

Checks:

* **[V] 30-minute horizon is correct.** I rebuilt `n_incidents_in_window`, `label_class` and `target` for every decision point of all 180 runs at 900, 1800 and 3600 s (3.63 M rows each) and compared with the stored labels: **0 mismatches** in any field, 0 active-mask disagreements, decision timestamps identical.
* **[V] The boundary convention cannot matter here.** Onsets lie on a 30 s grid and decision times on a 5 s offset, so no onset ever equals `t` or `t+H` (minimum distance 5 s).
* **[V] The data-sufficiency exclusion is independently reproduced** (own implementation of warm-up, freshness and coverage) on 3 runs x about 2,840 decision points: 0 disagreements.
* **[V] Features are strictly as-of.** I recomputed four features (`cpu_mean_300`, `mem_max_900`, `disk_root_last`, `rx_rate_mean_300`) from raw observations using only observations with observation time and arrival time <= t, for 160 rows: 640 comparisons, **0 mismatches**. **[U]** Rows where the arrival rule would change a value (buffered replays) did not occur in the sample (0 of 640), so that branch is only checked by code reading; such rows are mostly excluded as insufficient data anyway.
* **[V] Unscorable windows are not treated as negatives in training.** `eligible_view` keeps only positive and negative. **But** [V] they stay in the false-alarm count (section 8).
* **[V] Positive labels often precede any visible signal.** In 12.3 % of positive rows (net_flood: **56.5 %**) the latent precursor has not yet started, because a window opens up to 30 min before onset while precursor lead can be as short as 2 min (net_flood median lead 750 s; only 4.4 % of net_flood leads reach 1800 s). That is irreducible label noise for a model.
* **[V] Negatives:** 3.9 % lie inside a twin fault window (intended hard negatives); 5.3 % lie after a future incident's precursor began but more than 30 min before onset. Neither is an error; both lower achievable precision.
* **[V] Telemetry loss does not hide positives:** 0.73 % of insufficient-data rows lie within 30 min before an onset, against a 1.06 % base rate.
* **[V] Is the latent PRECURSOR visible in observed, noisy telemetry?** For 150 sampled PRECURSOR incidents on mem/disk/cpu, the shift between the 10 min before onset and 60 to 120 min earlier is at least 3 noise sigma in **75 %** (median 6.3 sigma; cpu_runaway 60 %). So roughly a quarter of PRECURSOR labels are only weakly visible. net_flood and the rx/tx side of pool_exhaustion were not sampled.

### Reconstructed examples (from raw fault events; each matches the stored label row)

| Example | Raw events | Decision time | To onset | Feature cutoff | Label |
|---|---|---|---|---|---|
| `mem_leak` PRECURSOR, r0000, f014 | fault start 15:37:30, precursor start 16:12:30 (lead 9,060 s), onset 18:43:30, recovery 18:57:00, mask end 19:12:00 | 17:58:05 | 2,725 s | obs <= 17:57:59 (mem 78.16) | negative (onset is beyond 1800 s although the precursor began 1 h 46 min earlier) |
| | | 18:23:05 | 1,225 s | obs <= 18:22:58 (mem 78.70) | **positive** |
| | | 18:40:05 | 205 s | obs <= 18:39:58 (mem 86.18) | **positive** |
| `net_flood` ABRUPT, r0005, f004 | fault start 00:24:00, precursor start 00:24:30, onset 00:25:30 (lead 60 s < 120) | 23:39:35 | 2,755 s | obs <= 23:39:30 | negative |
| | | 00:04:35 and 00:21:35 | 1,255 s and 235 s | obs <= 00:04:30 / 00:21:30 | **unscorable** (an incident is certain but not scorable) |
| `silent_crash` SILENT, r0002, f006 | onset = fault start 10:02:00, recovery 11:25:30, mask end 11:40:30 | 09:16:35 | 2,725 s | obs <= 09:16:28 | negative |
| | | 09:41:35 and 09:58:35 | 1,225 s and 205 s | obs <= 09:41:29 / 09:58:29 | **unscorable** |
| `mem_leak` twin, r0000, f010 | start 09:46:00, peak 10:37:00 (92.1 % against theta 95.3 %), resolved 10:44:30 | 10:36:05 | no incident | latest mem observation 83.99 | negative (hard negative) |

Full detail, including latest observation per series, is in the JSON (`label_examples_reconstructed_from_raw_events`).

## 5. Split integrity and leakage

* **[V] Counts are exact** (90 / 30 / 40 / 20); run ids, host ids, hostnames, agent ids and run start times are all unique; 0 cross-split duplicate rows on the six level features over 3.55 M rows (2,727 duplicates inside a split, 0.08 %).
* **[V] Run-level separation equals host-level separation by construction:** `host_id = sim-<run_id>`, one host per run. There is no host reuse across splits.
* **[V] No feature carries IDs, timestamps, labels or simulator metadata.** The 196 features come from the six metric series; the run-position feature `elapsed_s` is the only risky one and training excludes it.
* **[V] No single feature separates the classes trivially:** the best univariate AUC on a train sample is 0.77 (`mem_last`); none exceeds 0.9 or falls below 0.1.
* **[V] Training code is clean on selection:** only `train` and `val` are requested (asserted in `load_view`); imputer, scaler and models are fitted on train only; the threshold is chosen on val; no hyper-parameter search. **[U]** That test data was never loaded cannot be proven from artifacts; the saved report says `test_splits_used: false`, and no test-set outputs exist.
* **Run-level separation is not scenario-level generalisation.** **[V]** All four splits come from the same generator with the same six families, thresholds, shapes and benign processes. A test run is a new random host that suffers faults drawn from the same distributions; it shows generalisation to new random hosts of the same simulator, not to new failure mechanisms. The nearest-neighbour distance in host-profile space is the same from test to train as within train (3.08 against 3.25).
* **OOD is weaker than it looks. [V]** Of the 20 OOD runs, **10 are `cache_node` runs that are statistically identical to train** in all 15 host-profile parameters and all fault parameters tested (KS p > 0.05; the archetype only changes peak hour and a service name). The other 10 are known archetypes with **2.5x slower ramps**: their ramp/duration parameters really are out of range (`net_flood` decay 92 % above the train maximum, `cpu_runaway` ramp 45 %, `mem_leak` D_min 33 %, `disk_fill` 29 %, `pool_exhaustion` 20 %), while amplitudes are unchanged. So OOD = 10 slowed runs plus 10 relabelled IID runs; it tests slower dynamics of the same mechanisms, nothing else. Quantity of independent OOD evidence: 111 PRECURSOR incidents across 20 runs.

## 6. Features and missing data

* **[V] 196 features, 25 with no variance in the sample, 4 permanently NaN.** The 4 (`rx/tx_rate_std_300`, `rx/tx_rate_slope_300`) need at least 5 valid pairs, but a 300 s window at 60 s cadence holds at most 4. `*_n_300` and `*_pairs_300` are near-constant for the same reason. **52 of 171** usable features correlate above 0.98 with an earlier one (the `*_cov_*` columns are almost perfectly correlated across series because all six series arrive together). Cadence and age features are near-constant at a fixed 60 s cadence.
* **[V] Missing telemetry is invisible to the model.** The sufficiency rule removes stale or sparse rows before features are built, so **0 of 2,366,740 scored train/val rows** have a NaN in `cpu_mean_300`, `mem_last` or `disk_*_last`, and every `*_missing_*` indicator is constant 0. The model never sees what missing telemetry looks like, and these rows (1.0 % of decision points) are in no metric.
* **[V] The missing-as-zero defect** (`ml/predict.py:71-73` and the rule scorer in `ml/train_baselines.py`): `np.nan_to_num(..., nan=0.0)` makes an absent utilisation count as 0 % and `predict_risk` ends with `nan_to_num(risk, nan=0.0)`. For `logreg` a NaN becomes the train median (a typical host); for `xgboost` it follows an arbitrary learned default branch because no NaN was seen in those columns.
  * **Impact:** latent in every reported metric (the path is never exercised), but at inference an agent that goes silent produces risk 0 for the rule and typical-host risk for logreg, so a host whose telemetry is vanishing looks healthy. That is the worst direction for an outage predictor.
  * **Recommendation (not applied):** have `predict_risk` return NaN or an explicit `unknown` status when required inputs are stale or absent; raise a separate telemetry-lost alert; never impute to 0; remove the always-zero `*_missing_*` columns or train with rows that carry missingness; fail the rule scorer closed.

## 7. Realism

| Level | Can it be evaluated here? | Finding |
|---|---|---|
| Structural validity | **Yes** | Passes: 0 schema, semantic, footprint, timestamp and label-derivation failures. |
| Statistical similarity | Only marginally | Synthetic lag-1 autocorrelation 0.87 to 0.95 (SMD median 0.84, PSM 0.985). Fraction of consecutive equal values 0.2 to 0.4 % (SMD median 5.1 %): real series contain flat and quantised stretches that the simulator never produces. |
| Temporal realism | Partly | Lag-1440 autocorrelation is 0.12 to 0.18 for synthetic series against 0.745 in PSM (index assumed to be minutes), so the simulator has much weaker long-range persistence than the one real-like source available. Cross-series correlation is low (median 0.11). Incident timing cannot be compared. |
| Causal realism | **No** | No available dataset records resource state before real outages. Precursor mechanisms are the author's assumptions. |
| Real-world predictive validity | **No** | Needs real incidents with impact times. None exist here, and neither SMD nor PSM can validate 30-minute outage labels. |

No feature mapping to SMD or PSM was made, and matching ranges prove nothing. These comparisons are directional only.

## 8. Evaluation methodology (existing)

* **[V] Not a valid performance claim.** Saved results use 2 train runs (10 positive windows) and 1 validation run (**2 positive windows**). Validation PR-AUC: rule 0.0074, logistic regression 0.0239, XGBoost 0.0045, against prevalence 0.0030, which is chance level. XGBoost reaches train PR-AUC 1.0 against 0.0045 on validation, a textbook overfit; with XGBoost's threshold it alerts on about 46 % of validation rows (9,024 false positives). Even if both validation incidents were found (recall 1.0), the 95 % Wilson interval for 2 incidents is [0.34, 1.00].
* **[V] The saved artifact predates the current code:** the current `train_baselines.py` writes deployed-style alert statistics (false alarms per host-day) but the saved `report.json` has none. So **no false-alarm number exists anywhere** against the protocol's budget of 1 per evaluable host-day.
* **[V] Threshold:** maximum F1 on validation rows, which is not tied to the false-alarm budget and makes validation metrics optimistic (the report says so).
* **[V] Metric level:** decision-point rows at 60 s spacing, about 30 near-identical positive rows per incident, so the effective sample is the number of incidents, not rows. No incident-level recall, no warning lead time, no calibration (reliability, Brier or ECE) exist. One seed (7), no repeats.
* **[V] False-alarm accounting flatters the system:** an alert is false only if no incident of any kind begins in the window, so alerts before ABRUPT and SILENT incidents count as correct although those windows were never scorable, and the denominator includes unscorable rows.
* **Power available on the full splits [V]:** PRECURSOR incidents: train 455, val 132, test_iid 201, test_ood 111. A recall of 0.8 over the 132 validation incidents has a Wilson interval of [0.72, 0.86], so incident-level evaluation is feasible once the full splits are used.

## 9. Prioritised findings and what must be fixed before training

| # | Severity | Finding | Status | Fix before training |
|---|---|---|---|---|
| 1 | **Critical** | Saved ML results come from 2 of 90 train and 1 of 30 val runs, at chance level, overfit, no false-alarm figures | [V] | Use the full splits (`features_full`, `labels_full` exist); do not quote the saved numbers anywhere |
| 2 | **Critical** | 30.8 % of incidents (401 of 1,300: all 300 `silent_crash` plus 101 ABRUPT in `net_flood` and `cpu_runaway`) are unpredictable by design and are dropped from training and recall rows | [V] | Report recall over all incidents with a separate "predictable by design" line; state the ceiling |
| 3 | **High** | Missing-as-zero in inference; missingness never seen in training | [V] | Section 6 recommendation; add a telemetry-lost path |
| 4 | **High** | Incident = simulator saturation threshold; no external validity | [V] | State this in every claim; add outage types not tied to a utilisation crossing before any realism claim |
| 5 | **High** | Evaluation protocol: row-level metrics, F1 threshold, no incident recall or lead time, unscorable windows count as correct alerts, no calibration, single seed | [V] | Incident-level match-1 evaluation, lead time, false alarms per evaluable host-day with the threshold set on validation to meet the budget, calibration, bootstrap CIs by run, at least 5 seeds |
| 6 | **High** | OOD is half label-only and tests only slower ramps | [V] | Treat `cache_node` runs as IID; add genuinely new regimes (new fault shapes, threshold distributions, noise levels, cadences) |
| 7 | **Medium** | One master seed; seed1 partial; `code_hash` mismatch (`distinct.py`) | [V]/[U] | Generate more seeds when approved; regenerate the manifest hash or document the edit |
| 8 | **Medium** | 12 % of positive rows (net_flood 56 %) precede any precursor | [V] | Report achievable-recall ceiling per family; consider a minimum-visible-precursor criterion |
| 9 | **Medium** | Feature schema: 4 dead features, 25 zero-variance, 52 redundant | [V] | Version-bump the schema without them |
| 10 | **Medium** | Realism gaps: weak persistence, no flat or quantised stretches, no cross-host fleet context, gaps independent of faults | [V]/[I] | Calibrate noise and persistence on real traces from a customer pilot |
| 11 | **Low** | ~25 % of PRECURSOR labels only weakly visible in observed telemetry | [V] (sampled) | Extend the sample to net_flood and report |

**Do not train until items 1, 2, 3 (inference side) and 5 are decided.** Items 4 and 6 do not block controlled experiments but do block every external claim.

## 10. Recommended next actions (nothing implemented)

1. Approve a full-split evaluation design (incident-level, budget-based threshold on val, multiple seeds, lead time, calibration) before any training.
2. Approve the predictor fix for missing inputs (return unknown instead of zero) as a small, separate change.
3. Decide whether to generate additional master seeds and a harder OOD split.
4. Update `docs/KNOWN_LIMITATIONS.md` and the saved-model report wording to reflect this audit.
