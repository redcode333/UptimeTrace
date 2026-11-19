# External dataset adoption: audit and decision (2026-11-18)

Scope: the **ML research workstream only**. Nothing here touches MCP, the frontend, backend monitoring or production services. No model has been trained on external data. All numbers below come from the raw files (`scripts/audit_external_datasets.py`, report in `docs/evidence/external_audit.json`) unless marked *(source)*, meaning stated by the publisher but not verifiable from the files.

## 1. State of the existing pipeline (re-verified, not assumed)

* Synthetic dataset `uptimetrace-m31-seed0`: **180 runs, split 90 / 30 / 40 IID / 20 OOD**; sidecars: 2,429 faults, 1,300 merged incidents, 12,794 benign episodes, 1,465 telemetry gaps; view cadence 60 s with jitter. Observation files for all 180 runs. (The `seed1` dataset has only 108 of 180 observation partitions and is not used.)
* Full-data artifacts exist: features for all 180 runs (196 features from 6 named series: `cpu`, `mem`, `disk_root`, `disk_data`, `rx`, `tx`, windows 300/900/3600 s, NaN never zero) and labels for 3 horizons x 180 runs.
* **The saved model report is still the small-subset one**: train = 2 runs / 10 positive windows, validation = 1 run / 2 positive windows; thresholds chosen by F1 on validation ("optimistic"). Validation PR-AUC: rule 0.0074, logistic regression 0.0239, XGBoost 0.0045, against prevalence 0.003. No held-out IID/OOD result exists. The missing-as-zero rule (`ml/predict.py:71-73`) is unfixed.
* Incidents carry real `onset`, `recovery`, `cooldown_end` and `impact_*` timestamps, which is what makes outage labelling valid for this dataset.

### Two separate tasks

| | **A. Anomaly detection** | **B. 30-minute outage-risk prediction** |
|---|---|---|
| Question | Is this window unusual relative to normal behaviour? | Will an incident begin within 1800 s? |
| Label needed | any defensible anomaly label | incident **onset/impact timestamps** (horizon is defined from them) |
| Data today | simulator (normal-only windows derivable) | simulator only |
| External data usable? | **yes, as evaluation** | **no** (see below) |

Anomaly labels are never converted into outage labels in this repository.

## 2. Audit evidence per dataset

| | **SMD** | **PSM** | **AIOps KPI (2018)** | **NAB** |
|---|---|---|---|---|
| Source (pinned) | github.com/NetManAIOps/OmniAnomaly @ `7fb0e0a…` | github.com/eBay/RANSynCoders @ `948e61e…` | github.com/NetManAIOps/KPI-Anomaly-Detection @ `d06bda1…` | github.com/numenta/NAB @ `ea702d7…` |
| Paper | Su et al., KDD 2019 | Abdulaal, Liu, Lancewicki, KDD 2021 | AIOps Challenge 2018 (no citation in repo) | Ahmad et al., Neurocomputing 2017 |
| Retrieved | 2026-11-18 17:30Z | 17:31Z | 17:31Z | 17:31Z |
| Archive SHA-256 | `024cb146…c6db` | `7b4a4e3b…fa22` | `2f174d7a…f933` | `d7bd94c1…20eb` |
| **Data licence** (read from files) | **MIT, dataset-specific** `ServerMachineDataset/LICENSE` (© NetManAIOps-SMD) | **CC BY 4.0** `data/LICENSE` (© eBay Inc); code is BSD-3 | **only a repo-level MIT** (© NetManAIOps_KAD); no dataset terms | repo-level MIT (© Numenta); neither README makes a separate data-licence statement |
| Entities | 28 machines (groups 1/2/3 = 8/9/11) | 1 pooled series | 26 KPI series (Preliminary train) | 58 series (47 real-world) |
| Features | 38 dims, **anonymised, min-max scaled to [0,1]**, no names | 25 anonymised features (`feature_0..24`) | 1 value per series (univariate) | 1 value per series (univariate) |
| Time points | train 708,405 / test 708,420 | train 132,481 / test 87,841 | train 2,476,315 / test 2,345,211 | 365,558 |
| Sampling (from files) | **none stored** | relative minute index, step = 1 (not wall-clock) | 60 s to 300 s depending on KPI | 300 s (48), 3600 s (7), 600 s (2), 1800 s (1) |
| Missing values | 0 NaN | **4,195 NaN cells in 2,697 train rows**; test 0 | 0 | not counted (CSV values only) |
| Labels | point 0/1 + contiguous **segments**; 327 segments, median 11 rows, max 3,161; 4.16 % of test rows | point 0/1; **72 segments, median 5, max 8,861 rows**; **27.8 %** of test rows | point 0/1 per KPI; 933 segments; 2.16 % of train. **Preliminary `test.csv` has no labels**; labels for the final phase are in separate zip/hdf (not extracted here) | **windows** (start/end): 116 windows over 52 series |
| Outage/impact timestamps | **none** | **none** | **none** | **none** |
| Size | raw 464 MB; normalised 69 MB | raw 103 MB; normalised 17 MB | raw 212 MB (+176 MB / 100 MB when the finals zips are expanded) | 9.6 MB |
| Notable defects | interpretation files disagree with label segments on **3 of 28** machines (`machine-1-3`, `3-2`, `3-3`); 192 of 1,064 train columns are constant | very long segment = 10 % of test; train has missing cells | KPI-level only, licence unclear | univariate; mixed domains incl. non-infrastructure (tweets, ads) |

*(source)* only: SMD is described as 5 weeks from a large Internet company, split into equal train/test halves per machine; PSM is described as eBay server-node metrics. Neither claim, nor SMD's sampling interval, can be checked from the files.

### What this means for the two tasks

* **No candidate has incident or impact timestamps** (checked: no such file or column in any of the four). Anomaly points/segments are expert marks on telemetry, not outage records. Therefore **none can validate Task B**, and none may be relabelled into outages.
* **Feature compatibility:** UptimeTrace's features are defined on named host series (cpu, memory, disk, network). SMD/PSM columns are anonymised, so they **cannot be mapped** to those series and **cannot flow through the UptimeTrace feature pipeline**. External evaluation can only test *generic multivariate detector families* on each dataset's own columns; it cannot validate UptimeTrace's feature engineering or its trained models.

## 3. Decisions

| Dataset | Decision | Exact reason |
|---|---|---|
| **SMD** | **ADOPT (external evaluation, Task A)** | Closest domain (multi-metric server telemetry); 28 entities allow per-entity temporal train->test evaluation; unambiguous dataset-specific MIT licence; zero missing values keeps the first comparison clean; segment labels support event-level metrics and detection delay. Limits recorded above (anonymised, scaled, no time axis, no impact timestamps, label/interpretation disagreement on 3 machines). |
| **PSM** | **ADOPT (secondary external evaluation, Task A)** | Clear CC BY 4.0 data licence with attribution duty; real missing values in train give a genuine missing-data test; 25 columns on one pooled entity. Caveats: 27.8 % anomalous test rows and one 8,861-row segment make point-level and point-adjusted scores uninformative, so only event-level and fixed-budget metrics will be reported; no wall-clock time. |
| **AIOps KPI 2018** | **DEFER** | Licence is unclear (repo MIT only; competition data terms not stated); univariate service KPIs, not host resources; the Preliminary test file is unlabeled and labelled finals need separate extraction. Revisit only if a univariate detector sanity benchmark becomes necessary and the data terms are confirmed in writing. |
| **NAB** | **DEFER** | Univariate, mixed (partly non-infrastructure) domains, window labels; small and permissively licensed but does not answer "do host-metric detectors generalise?". Optional later as a streaming sanity check. |
| SMAP/MSL, SWaT/WADI | **Not audited, not adopted** | Optional per the brief. Spacecraft telemetry and industrial-control testbeds are far from IT hosts; SWaT/WADI are, to my knowledge, distributed on request with terms (not verified here). |

## 4. Recommendation

1. **Used for model training: none for product models.** Anonymised features cannot feed UptimeTrace's feature pipeline, and no external set has impact timestamps. Product anomaly models stay trained on simulator normal-only windows (and later on customer data). Per-dataset benchmark fitting (each detector fitted on that dataset's own train split) is permitted solely to produce the external-evaluation numbers.
2. **External evaluation only: SMD (primary), PSM (secondary).** Results are reported per dataset and never merged; **no cross-dataset transfer** (SMD<->PSM or either<->simulator), because feature semantics differ and are undefined.
3. **Rejected for Task B: all of them.** Outage-risk prediction remains a synthetic-only result and must be described as such. Real-world outage validation requires real incident/impact records, which only customer data will provide.
4. **Deferred:** AIOps KPI, NAB. **Not pursued:** SMAP/MSL, SWaT/WADI.

## 5. Provenance and pipeline (built; no training run)

* Raw files stay outside the repository and outside OneDrive (`$UPTIMETRACE_DATA_ROOT/<dataset>/raw/`; moved 2026-11-19 from `C:/Users/<you>/ut-external-data` to `D:/UptimeTrace-ML/external-data`), immutable, with `PROVENANCE.json` (repository, commit, archive SHA-256, retrieval time, licence file hash, attribution, paper).
* `backend/ml/external/` holds one adapter per dataset (`smd.py`, `psm.py`) plus `common.py` and `build.py`: values copied as float32 with **no scaling and no imputation**, NaN preserved, int8 labels, explicit `t_index` (row order, not time), a manifest with raw-file hashes, licence and attribution. Adapters refuse to write inside the raw tree, refuse mismatched label alignment or non-numeric content, and are deterministic.
* Built outputs: SMD `normalised/v1.0` = 56 files, 1,416,825 rows, 0 NaN; PSM `normalised/v1.0` = 2 files, 220,322 rows, 4,195 NaN. Both match the independent audit totals.
* Tests: `backend/tests/external/test_adapters.py`, 7 passed (tiny fixtures: raw tree untouched, NaN preserved, deterministic output, misalignment/shape/label errors rejected, output inside raw refused).
* ML environment (moved 2026-11-19): venv at `D:\UptimeTrace-ML\venv`, built from `backend/requirements.lock` (hash-pinned; `uv pip sync --require-hashes requirements.lock`). `pandas` and `joblib` (and `scipy`) are now declared in the `ml` extra of `backend/pyproject.toml`. Set `UPTIMETRACE_DATA_ROOT=D:\UptimeTrace-ML\external-data`.
* Reproduce: `python -I scripts/audit_external_datasets.py --root <data-root> --out docs/evidence/external_audit.json`, then `python -m ml.external.build --dataset smd|psm --data-root <data-root>` (from `backend/`).

## 6. Evaluation protocol to be accepted BEFORE any model is trained

* **Splits:** the published train/test halves are kept. No validation split is published, so validation = the **temporal tail (last 20 %) of each train series**; the test split is never used for tuning. No random row splits.
* **Normal-only assumption:** train is unlabelled and assumed mostly normal; this is a stated limitation.
* **Detectors:** robust z-score threshold baseline; the existing rolling robust baseline; Isolation Forest; ECOD; COPOD (needs the `pyod` dependency, to be added with justification and pinned). Detectors that cannot take NaN get a documented policy (median imputation fitted on train + explicit missingness indicators); NaN is never zero-filled.
* **Threshold:** chosen on validation only, from a **predeclared false-alert budget** (alerts per 1,000 rows, since no wall-clock exists), then frozen.
* **Metrics (test):** event-level recall (segment counts as detected if an alert falls inside it, or within a declared tolerance after onset), detection delay in rows, false alerts per 1,000 non-anomalous rows at the frozen threshold, point-level PR-AUC and prevalence side by side. **Point-adjusted F1 is not a headline metric** (it rewards any single hit inside a long segment). Per-machine results for SMD plus a macro summary. Missing-data behaviour on PSM is reported separately.
* **Honesty rules:** no claim that results validate outage prediction or production performance; synthetic and external results are never averaged; incompatible features are listed, not forced.

## 7. Open items

* Acceptance of this audit and protocol (needed before training).
* `pyod` dependency decision; extraction of the KPI finals files only if KPI is un-deferred.
* The synthetic-data evaluation (Task B full-data train/validate/freeze/test) and the missing-as-zero fix remain open and independent of this workstream.
