# Evaluation results

**Read this first.** Every number below is from **seed 0 of the synthetic simulator, on the validation split**. Validation was used to choose thresholds and to compare variants. The held-out test splits (`test_iid`, `test_ood`) have not been opened, and no five-seed evaluation exists yet (seeds 1 to 4 are not generated). These are not real-world accuracy, not held-out results and not calibrated probabilities.

Source: the seed-0 evidence set (`ML_EVIDENCE.md`, `evidence/*.json`, SHA-256 hashes in `evidence_hashes.json`). The evidence files are kept outside the repository.

## Anomaly detection (300-second window)

Validation: 604,800 rows, 602,541 scorable, 3,328 positive rows, 419.98 host-days. Learned thresholds are the 99th percentile of benign calibration scores. The fixed rule uses 85%.

| Detector | PR-AUC | Precision | Recall | F1 | False-alarm episodes per host-day | Recall (all faults) | Recall (observable faults) |
|---|---|---|---|---|---|---|---|
| ECOD (learned) | 0.067 | 0.148 | 0.156 | 0.152 | 3.00 | 0.350 | 0.352 |
| Isolation Forest (learned) | 0.025 | 0.043 | 0.124 | 0.064 | 3.77 | 0.332 | 0.333 |
| COPOD (learned) | 0.038 | 0.090 | 0.093 | 0.092 | 2.05 | 0.257 | 0.258 |
| Fixed 85% threshold (rule) | 0.041 | 0.052 | 0.137 | 0.076 | 4.63 | 0.276 | 0.277 |
| 5-minute z-score (rule) | 0.041 | 0.070 | 0.119 | 0.088 | 0.98 | 0.149 | 0.150 |

Window sensitivity (60 s and 600 s) is in `ML_EVIDENCE.md`. ECOD is the best learned detector on PR-AUC at every window tested, and every detector is weak.

## Outage prediction (30-minute horizon)

Validation, row-level metrics at each model's validation threshold. Incident recall counts precursor incidents detected.

| Model | PR-AUC | Precision | Recall (row) | F1 (row) | False alarms per host-day | Incident recall | Brier skill | ECE |
|---|---|---|---|---|---|---|---|---|
| Rule | 0.056 | 0.146 | 0.150 | 0.148 | 2.00 | 0.470 | n/a | n/a |
| Logistic regression | 0.045 | 0.070 | 0.201 | 0.104 | 3.70 | 0.439 | -22.25 | 0.321 |
| XGBoost | 0.087 | 0.123 | 0.264 | 0.168 | 2.30 | 0.508 | -17.16 | 0.283 |

Brier skill is negative for every model: none beats the base rate on calibration.

## Calibration (outage model, validation evaluated once)

Negative thinning stride 10. Half-A scale_pos_weight 14.490. Analytic correction ln(w x stride) = 4.976. Isotonic calibrator fitted on 884,214 training rows (half B).

| Variant | Brier | ECE | Skill vs climatology | PR-AUC |
|---|---|---|---|---|
| Production model, as reported | 0.11913 | 0.2831 | -17.161 | 0.0870 |
| Half-A model, raw | 0.09892 | 0.2337 | -14.080 | 0.0745 |
| Half-A, analytic correction | 0.00693 | 0.0036 | -0.057 | 0.0724 |
| Half-A, isotonic (training-only) | 0.00638 | 0.0036 | 0.027 | 0.0692 |

Calibration lowers Brier and ECE. It does not improve ranking: PR-AUC stays about the same, as expected from monotone transforms. The skill values are still near zero or negative, so these scores are not reliable probabilities.

## Live adapter checks (not an evaluation)

These checks show the live path reproduces the offline features and scores. They are not accuracy measurements.

- Feature parity (run `r0000`, 50 decision points, 192 columns): no NaN mismatches; largest relative difference about 1e-7 (float32 storage).
- Score parity through the adapter (six decision points): maximum absolute difference 4.5e-6.
- ECOD artifact parity (5,000 validation rows): exact match (maximum difference 0.0), identical threshold.
- Hosted example, host `web-10` (one hour of live telemetry): experimental risk score 0.0071, validation threshold 0.905. Single reading, not evaluated.

## What is not complete

- Five-seed evaluation (seeds 1 to 4 not generated).
- Held-out evaluation (`test_iid`, `test_ood`): not run, pending protocol freeze approval.
- Real-world evaluation: none.
