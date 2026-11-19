# ML evaluation protocol v1.2, synthetic 30-minute outage prediction

Status: **FINAL-REVIEW AMENDMENTS B1 to B6 APPLIED ON TOP OF A1 to A6; NOT FROZEN.** Nothing here has been run; no model was trained, tuned or loaded, and no test data was read or scored for this revision. The amendment log is section 17; the earlier hashes are recorded there. Machine-readable twin: `docs/evidence/ml_evaluation_protocol.json`. Verified inputs: `docs/SYNTHETIC_DATASET_AUDIT.md` and `docs/evidence/synthetic_dataset_audit.json`; expected counts: `docs/evidence/ml_evaluation_protocol_expected_counts.json` (script `scripts/protocol_expected_counts.py`).
Basis: simulator spec v4.5.1 sections 13 to 16 (match-1, fa-2, operating point, claims policy), preserved verbatim with source, version and hashes in `docs/SIMULATOR_SPEC_V4_5_1_SECTIONS_13_16.md` (amendment B6). Where this protocol is **stricter** than the spec it says so in the clause and in section 14 (items D1 to D3).

Every report produced under this protocol opens with: **"These results measure recovery of simulator-defined faults and do not demonstrate production outage-prediction accuracy."**

## 1. Task and unit of evaluation

* Task: at each 60 s decision time `t` of a host, raise an alert if an outage-class incident will begin in `(t, t + 1800 s]`.
* **Primary evaluation unit: the merged incident** (1,300 in the dataset), classified by its primary fault as PRECURSOR, ABRUPT or SILENT. Decision rows are used only for exposure, scoring and calibration.
* Primary horizon `H = 1800 s`. Minimum useful lead `L_min = 120 s`. Alert de-duplication `D = 900 s`. Decision interval `Δ = 60 s`, decision time `t_j = run_start + 5 s + 60 s·j`.
* Horizons 900 s and 3600 s are optional sensitivity analyses with their own thresholds; they never appear in a headline.

## 2. Data and split usage

Dataset `uptimetrace-m31-seed0-2723fc6789d8`, labels `artifacts/labels_full`, features `artifacts/features_full` (feature version 1.0, labeler 1.0). The 7-run subset in `artifacts/labels` and `artifacts/features` is **never** used.

| Split | Runs | Evaluable host-days | Incidents | PRECURSOR | ABRUPT | SILENT | Unpredictable | Allowed use |
|---|---|---|---|---|---|---|---|---|
| train | 90 | 1,232.6 | 650 | 455 | 45 | 150 | 30.0 % | fit models and preprocessing; diagnostics |
| val | 30 | 411.0 | 195 | 132 | 18 | 45 | 32.3 % | threshold, model-type selection, calibration fit |
| test_iid | 40 | 547.8 | 293 | 201 | 24 | 68 | 31.4 % | one frozen evaluation |
| test_ood | 20 | 273.2 | 162 | 111 | 14 | 37 | 31.5 % | one frozen evaluation (reported as two subgroups) |
| **all** | 180 | 2,464.5 | **1,300** | **899** | **101** | **300** | **30.8 %** (401) | |

* `test_ood` = 10 `ood_slow` runs (known archetypes, 2.5x slower ramps; 87 incidents, 65 PRECURSOR) + 10 `ood_cache_node` runs (statistically identical to train per the audit; 75 incidents, 46 PRECURSOR). Both subgroups are reported separately and pooled; the pooled number is never presented as evidence of robustness. IID and OOD are **never** pooled with each other.
* Split membership is fixed by SHA-256 of the sorted run-id list per split (recorded in the JSON). Any other membership is a protocol violation.
* **Train:** models and every preprocessing step (imputer, scaler, class weights) are fitted on train only. The default training view uses positive (PRECURSOR onset in window) = 1, negative = 0, unscorable masked, excluded rows dropped. Negative thinning (every k-th decision, `decision_index % k == 2`) is allowed only for memory, must be recorded, and is never applied to val or test. The **operational target** view (unscorable = 1; spec section 13) is trained and evaluated only as the separately reported secondary experiment E2 (section 2.1). Features: schema 1.0 minus `elapsed_s` (195). No feature selection, no hyper-parameter search; the three models are the existing `rule`, `logreg` and `xgboost` with their current fixed settings. Any other model, setting or feature set is an amendment (section 12).
* **Val:** may be read freely for threshold selection (section 8), post-hoc calibration fitting, and choosing the primary model type. Nothing else.
* **Test:** unreachable until the freeze (section 11). Threshold, model, calibrator, reliability-bin boundaries and metric code are fixed before the first test file is opened.

### 2.1 Experiments (amendment A3)

| | E1 (primary) | E2 (secondary, separately reported) |
|---|---|---|
| Training view | default: positive = PRECURSOR onset in window, negative, **unscorable masked** | operational target: positive = 1, negative = 0, **unscorable = 1** |
| Models | `rule` (once, deterministic), `logreg`, `xgboost` (five seeds each) | `logreg`, `xgboost` (five seeds each); `rule` is excluded because its scores do not depend on the training view (section 2.2) |
| Threshold, matching, budget, metrics, bootstrap | section 4 to 9, unchanged | identical procedure and budget, applied to the E2 models |
| Appears in headline tables | **yes** | **no**: own section, labelled "secondary experiment E2" |
| Used for primary-model selection | **yes** (E1 validation only) | **never** |

E2 is frozen at the same freeze point as E1 and evaluated once on the test splits after it. E1 and E2 are compared descriptively only; no E2 number may influence a choice of model, threshold, feature or protocol setting for E1. An E2 model alerting before ABRUPT/SILENT incidents is scored exactly like any other model (those alerts are strict false alarms, section 5).

### 2.2 The deterministic `rule` baseline (amendment B2)

`rule` (`max(cpu_mean_300, mem_last, disk_root_last, disk_data_last) / 100`) has no fitting and no randomness. It is evaluated **exactly once**, in **E1 only** (excluded from E2). The five training seeds `101` to `105` apply **only to `logreg` and `xgboost`**. Five identical `rule` runs are never produced and no artificial seed variability is attributed to it. Representation:

* **`frozen_run.json`:** exactly one `rule` entry, `{model: "rule", experiment: "E1", training_seed: null, deterministic: true}`, carrying one exact threshold, one candidate-set hash and length, one `boundary_selected` / `no_alert_selected`, one set of reliability-bin boundaries, one isotonic calibrator and one validation record. Total frozen entries: **21** = E1 (1 `rule` + 5 `logreg` + 5 `xgboost`) + E2 (5 `logreg` + 5 `xgboost`).
* **Threshold selection:** section 8 is run once for `rule` on validation.
* **Reliability bins:** derived once from the `rule`'s validation scores (section 7.5).
* **Chance diagnostic:** one draw from the reserved stream `numpy.random.default_rng([20261202, 0])`. Stream id `0` is reserved for deterministic models and is not a training seed, so the `rule`'s shifts differ from those of the seeded models and rule-versus-model comparisons in this diagnostic are descriptive only (section 7.9).
* **Summaries:** seed means, minima, maxima and standard deviations are computed over the `logreg` and `xgboost` seeds only. The `rule` appears as one row labelled "deterministic, 1 evaluation" with no seed statistics; its uncertainty is the run-level bootstrap alone. Counts such as "seeds selecting no-alert" are shown for `rule` as "1 evaluation: yes/no".
* **Primary-model selection:** the `rule`'s single validation `Recall_P` is compared with the seed means of `logreg` and `xgboost` (tie order unchanged, section 8).

## 3. Decision points, exclusions, exposure

For every decision point exactly one status, in this precedence (spec section 13):

1. `excluded_insufficient_data`: fails the data-sufficiency rule (warm-up < 3600 s, absent, stale, or window coverage < 0.5).
2. `excluded_active`: `t` in `[onset, oracle_mask_end)` of any incident (oracle active-incident mask).
3. Otherwise **evaluable**. With `O(t)` = merged incidents with onset in `(t, t+H]`: `positive` if `O(t)` has a PRECURSOR; `unscorable` if `O(t)` is non-empty without a PRECURSOR; `negative` if `O(t)` is empty.

Handling (all consequences are stated here so nothing is implicit):

* **Excluded points (either kind) emit no score and no alert, add no exposure, and carry no label.** They are never counted as negatives.
* **Unscorable points are evaluable.** They can emit alerts and they count as exposure. They are masked only from the row-level primary label view (section 7.5).
* `O(t)` is built from all merged incidents, with no exclusion filter, so an alert's classification depends only on incident onsets, never on whether later decision points are excluded.
* Exposure `E_h` = number of evaluable decision points x 60 s (decision-quantised, spec fa-2). Dedup-suppressed intervals stay in exposure. Expected exposure fraction is 97.6 to 97.9 % (mask + insufficient data remove about 2.2 %).
* **Pre-flight guard:** if any evaluable row has a NaN in `cpu_mean_300`, `mem_last`, `disk_root_last` or `disk_data_last`, the run aborts. The audit verified 0 of 2,366,740 rows; the guard exists so the missing-as-zero path of `ml/predict.py` can never be exercised silently. Models must be scored through a path that fails closed on NaN in required inputs.

## 4. Alert emission

Per host, per horizon, per frozen threshold `θ`, scanning evaluable decisions in time order:

```
last = None
for each evaluable decision j (time t_j, score s_j):
    if s_j >= θ:
        if last is None or t_j >= last + 900 s:  emit alert at t_j; last = t_j
        else:                                    suppressed += 1   # not an alert, no credit, not a false alarm
```

The suppression clock runs in real time across excluded stretches. Ties at `θ` alert (`>=`). Scores are float64 in `[0, 1]`; the evaluator asserts this (a NaN or an out-of-range score aborts the run), which is what makes `θ_none = nextafter(1.0, +inf)` an exact no-alert threshold and `θ_all = 0.0` an exact all-alert threshold (section 8).

## 5. Matching and alert outcomes

**Credit window** of a PRECURSOR incident with onset `o`: `o - 1800 s <= t <= o - 120 s` (closed). Only alerts inside it can be credited.

**Stage 1 (PRECURSOR matching).** Incidents in increasing onset; each takes the earliest still-unconsumed alert in its credit window, which is then consumed. One alert credits at most one incident and one incident is detected at most once. (Equal-length windows make this greedy optimal.)

**Alert outcome**: every emitted alert gets exactly one:

| Outcome | Definition | Hit / recall numerator | Strict false alarm (**budget**) | Spec false alarm | In precision numerator |
|---|---|---|---|---|---|
| `hit` | consumed in stage 1 | **yes** | no | no | yes (strict) |
| `duplicate` | not a hit; lies in the credit window of a PRECURSOR incident already credited to another alert | no | no | no | lenient only |
| `late` | not a hit/duplicate; a PRECURSOR onset lies in `(t, t+H]` but less than 120 s ahead | no | no | no | lenient only |
| `coincident_unpredictable` | `O(t)` is non-empty and holds only ABRUPT/SILENT incidents | **no** | **yes** | no | no |
| `false_alarm` | `O(t)` is empty | no | **yes** | yes | no |

* Check on completeness: a not-consumed alert inside a PRECURSOR credit window must belong to an already-detected incident (otherwise stage 1 would have matched it); the evaluator asserts this.
* **An alert is never credited because an ABRUPT or SILENT incident follows.** Such alerts are `coincident_unpredictable`, count as strict false alarms and as non-hits, and are listed in their own column. The spec's stage 2 (credit for ABRUPT/SILENT) is kept only as a **diagnostic** (section 7.9) and never feeds recall, precision or the budget.
* A mixed window (PRECURSOR and ABRUPT/SILENT onsets both inside `(t, t+H]`) is classified by its PRECURSOR content (hit, duplicate or late).
* Alerts cannot occur in excluded stretches, so no alert overlaps an excluded window by construction; an alert's horizon may extend into a later mask, which does not change its class.

## 6. Incident outcomes and accounting

* PRECURSOR: **detected** iff stage-1 matched. **Missed** otherwise, including an incident with no evaluable decision in its credit window ("unevaluable", counted as missed and reported; currently 0 in every split).
* ABRUPT and SILENT: **never "detected"**. Diagnostic status "coincidentally warned" (at least one alert, consumed or not, in `[o-1800 s, o-120 s]`), reported only in section 7.9.
* **Mandatory accounting block** at the top of every results table, per split and subgroup, never omitted or filtered: total incidents `N`; PRECURSOR `N_P`; ABRUPT `N_A`; SILENT `N_S`; unpredictable-by-design `N_U = N_A + N_S` and its share; unevaluable PRECURSOR; detected PRECURSOR `K`.
* Because the ceiling is `N_P / N` (about 0.69), the headline always includes **"predicted share of all incidents" = K / N**, next to recall on the predictable set.

## 7. Metrics

Pooled across the runs of a split or subgroup as ratios of sums, never as averages of per-run ratios. Every metric has a run-level bootstrap interval (section 9).

1. **Incident recall (primary)** `Recall_P = K / N_P`. Per-family `Recall_P[family]` for mem_leak, disk_fill, cpu_runaway, net_flood and pool_exhaustion (`silent_crash` has no PRECURSOR incidents). A family cell with fewer than 30 PRECURSOR incidents is flagged `LOW-N`; fewer than 10 reports the count only, no estimate.
2. **Predicted share of all incidents** `K / N`, plus the ceiling `N_P / N`.
3. **Incident-level precision** `strict = hits / alerts`; `lenient = (hits + duplicates + late) / alerts`. Alerts per evaluable host-day and per calendar host-day.
   * **Zero-alert rule (amendment B3).** When a model emits **no alerts** in the evaluated set (for example it selected the no-alert threshold), both precisions are reported as the literal **`N/A (zero alerts)`**. They are never shown as 0 or 1, and they are excluded from every average, comparison and plot (with "undefined" counted and printed). The model's alert count stays **0**, alerts per host-day 0, strict and spec false alarms 0, **`Recall_P` = 0** (`K = 0`, every PRECURSOR incident missed) and `K / N` = 0.
   * **Probability-based metrics are still computed** from the model's scores for a zero-alert model: Brier score and skill, the reliability table, ECE, calibration-in-the-large and row-level PR-AUC.
   * **Aggregation:** a seed mean of precision covers only the seeds with a defined precision and prints "defined in n of 5 seeds" (the single `rule` evaluation prints "defined" or "N/A"). In the bootstrap, a replicate whose resampled runs contain zero alerts has undefined precision and is **excluded from that metric's percentile interval**; the number of excluded replicates is reported, and if more than 5 % of replicates are undefined the interval is reported as "not estimable" rather than as a number.
4. **False alarms per evaluable host-day** (budget metric) = `(false_alarm + coincident_unpredictable) / Σ(E_h / 86,400)`. Also reported: spec rate (`false_alarm` only), calendar rate `N_FA / Σ(C_h)`, exposure fraction, and the non-evaluable time by status (`excluded_insufficient_data`, `excluded_active`, `no_decision`). Every false-alarm figure carries the label "conditional on the oracle active-incident mask and the data-sufficiency exclusion". The oracle mask uses ground truth no deployed system has; these are not deployed false-alarm rates.
5. **Row-level Brier score and calibration** (the one metric defined on rows, because calibration is a property of the 30-minute probability). Primary view: evaluable rows with unscorable masked (`y` = positive). Secondary "all-incidents" view: unscorable `y = 1`. Brier score, **Brier skill score** against the constant predictor `p_train` (train positive prevalence), reliability table on the **frozen validation-derived bins** defined below (mean predicted, observed rate, n, per-bin run-bootstrap interval), expected calibration error over those bins, and a calibration-in-the-large figure (mean predicted minus observed).
   * **Bin boundaries (amendment A2).** Per frozen model entry (section 2.2: one for `rule`; one per training seed and experiment for `logreg` and `xgboost`), computed **once, from validation scores only**: `S` = the sorted raw scores of all evaluable validation decisions (the same set used for threshold candidates). Interior quantile levels `q` in {50, 80, 90, 95, 98, 99, 99.5, 99.9} %; each boundary `b_q = S[min(n-1, ceil(q·n))]` (the "higher" quantile, 0-based index).
   * **Duplicate boundaries (deterministic).** First **discard every boundary equal to the smallest validation score** `S[0]` (it would make the first bin `(-inf, S[0])` empty). Then take the **unique** remaining boundary values in ascending order, `e_1 < ... < e_m`. Bins are `B_0 = (-inf, e_1)`, `B_i = [e_i, e_{i+1})`, `B_m = [e_m, +inf)`. Tied quantile levels therefore merge, never split. Every boundary is itself a validation score and is greater than the minimum, so **no bin is empty on validation**. If every boundary is discarded (for example a constant scorer), there is a single bin `(-inf, +inf)`, and this is reported. The **actual number of bins is `m + 1` (at most 9)** and is reported per model/seed together with the list of collapsed or discarded levels (for example a heavily tied scorer such as `rule` may yield few bins).
   * **Frozen and shared.** The boundaries are written to `frozen_run.json` at the freeze (section 11) and applied unchanged to `test_iid`, `test_ood`, `ood_slow` and `ood_cache_node`, so every split uses identical bins. Test scores outside the validation range fall in `B_0` or `B_m`. A bin that is empty on a test split is reported with `n = 0` and no estimate; boundaries are never re-derived from test data.
   * The same raw-score bins are used for the secondary val-fitted isotonic calibration (the bin axis stays the raw score; "mean predicted" is the mean calibrated value). Raw model scores are evaluated; a second, **val-fitted isotonic** calibration may be reported alongside. It never changes alerts (thresholds stay on the raw, monotone-equivalent score). Class-weighted training makes raw scores uncalibrated by design; this is expected and is reported, not corrected silently.
6. **Warning lead time** (detected PRECURSOR only): `lead = o - t_alert`, in `[120, 1800]` s. Median, p10, p90, mean, and counts in the buckets `[120, 300)`, `[300, 900)`, `[900, 1800]` s. Lead-conditional recall: matching re-run with `L_min = 300` and `600` s (credit windows `[o-1800, o-300]` and `[o-1800, o-600]`).
7. **Detection delay** (detected PRECURSOR only; oracle quantity): `delay = t_alert - p`, `p` = `precursor_start` of the incident's primary fault (the first moment the latent deviation exceeds 2 sigma). Median, p10, p90; share of detected incidents with `delay < 0` (alert before the latent deviation began). Censoring-aware companion: the share of **all** PRECURSOR incidents detected with `delay <= d` for `d` in {5, 15, 30, 60, 120} min (missed incidents count as not detected). The audit found about a quarter of PRECURSOR labels only weakly visible in noisy telemetry, so delay is reported with that caveat.
8. **Row-level PR-AUC** on the primary view: diagnostic only, with prevalence shown (spec section 16). It is not part of any headline or comparison.
9. **ABRUPT/SILENT chance diagnostic (amendment A4; required in the first evaluation; strictly diagnostic).** Per group (ABRUPT, SILENT) and per family: the number and proportion of incidents coincidentally warned, and the chance baseline from **1,000 seeded circular shifts**. It **never** contributes to `Recall_P`, `K / N`, precision, false-alarm counts, threshold selection or primary-model selection, and it is printed only in its own section, labelled "diagnostic: not prediction success". Poor SILENT recall is never pooled with PRECURSOR recall.
   * *Observed* proportion `p_obs` = incidents of the group with at least one emitted alert in `[o - 1800 s, o - 120 s]` (any alert, whether or not consumed in stage 1), over the group's incidents in the split.
   * *Shifted* replicate `r = 1..1000`: for each run draw an offset `d_r` uniformly from `{1, ..., 20159}` decision steps (the run has 20,160). Move every emitted alert of the run from decision index `j` to `(j + d_r) mod 20160`, **drop** alerts that land on an excluded decision (an alert cannot be emitted there, so it cannot suppress anything), apply the **circular dedup** defined below with the original 900 s interval, then recompute the proportion `p_r` pooled over runs.
   * Reported: `p_obs`, mean of `p_r`, `p_obs - mean(p_r)`, and the empirical one-sided p-value `(1 + #{r : p_r >= p_obs}) / 1001`.
   * Seed: offsets are drawn from `numpy.random.default_rng([20261202, training_seed])`, array shape `(1000, n_runs)`, runs in sorted run-id order; one fixed draw is reused for every model of that seed so models are compared on identical shifts. The deterministic `rule` uses the reserved stream id 0 (`[20261202, 0]`), so its shifts differ and rule-versus-model comparisons here are descriptive (section 2.2).
   * **Circular dedup (amendment B4): deterministic and independent of where the run is cut.** Alert positions live on a circle of `N = 20160` decision steps. The dedup gap is `15` steps (900 s / 60 s) and the forward distance from `x` to `y` is `(y - x) mod N`. Given the alert positions that remain after dropping excluded decisions:
     1. With 0 or 1 alerts, keep them (for a single alert the gap into it is `N`).
     2. The **gap into** alert `a` is the forward distance from the previous alert in circular order; cyclically the last alert (largest index) precedes the first (smallest index).
     3. Cut the circle at **every** gap that is `>= 15`. This yields maximal **chains** of consecutive alerts whose internal gaps are all `< 15`; each chain starts at an alert whose incoming gap is `>= 15`.
     4. Inside each chain apply the greedy earliest-first rule: keep the chain's first alert; keep a later alert iff its forward distance from the last kept alert is `>= 15`, otherwise drop it. A gap of exactly 15 keeps, matching section 4 (`t >= last + 900 s`).
     5. Chains are independent of each other, so the result depends only on circular positions: **rotating all positions by any constant rotates the result by the same constant** and no linear cut of the run enters. A chain may cross the boundary between index 20159 and index 0; its first alert is then the one with the larger index (alerts at 20155 and 2 are 7 apart, so 20155 is kept and 2 is dropped, even though a linear scan from index 0 would keep 2).
     6. **Precondition and abort rule:** the alerts come from section 4 on the unshifted timeline, so consecutive alerts are at least 15 apart on the line and only the pair (last, first) can be closer than 15 on the circle. Hence at least one gap `>= 15` always exists, so no chain is a full circle. Dropping excluded decisions only enlarges gaps. If the evaluator ever finds two or more alerts with no gap `>= 15`, it aborts.
   * Computed on the frozen alerts of validation (labelled "selected on validation") and of each test split. PRECURSOR incidents are not part of this diagnostic.
10. **Model comparison**: paired run-level bootstrap on the difference of `Recall_P` and of strict false alarms per evaluable host-day (same resampled runs). A model is "better" only if the 95 % interval of the difference excludes 0 on `Recall_P` **and** neither model's test verdict (section 8.1) is "budget exceeded" or "budget exceeded with confidence".

## 8. Threshold (operating-point) selection

Per frozen model entry (section 2.2: one for `rule`; one per training seed and experiment for `logreg` and `xgboost`), per horizon, on validation only:

1. Score every evaluable validation decision. Let `S` be the sorted scores, `n = |S|`.
2. **Candidate set (amendment A1).** The union of three parts, deduplicated and sorted ascending:
   * the **intermediate quantile candidates**, retained unchanged: tail masses `m_k = 10^(-1 - 4k/1999)`, `k = 0..1999` (0.1 down to 1e-5), `θ_k = S[min(n-1, ceil((1 - m_k)·n))]` (a "higher" quantile);
   * the **all-alert boundary** `θ_all = 0.0`. Every score lies in `[0, 1]` (the predictor clips), so every evaluable decision meets `score >= 0.0`; alerts are then limited only by the 900 s dedup;
   * the **no-alert boundary** `θ_none = nextafter(1.0, +inf)` = `1.0000000000000002`, strictly greater than the maximum possible score (not merely the largest observed validation score), so no decision can ever alert.
   * **Deduplication:** candidates are compared as exact float64 values; equal values are one candidate (for example when a quantile equals 0.0 or an observed maximum). The deduplicated list, its length (at most 2,002) and its SHA-256 are recorded.
3. **Evaluate every candidate, boundaries included, through the identical pipeline**: alert emission with dedup (section 4), matching and outcome classification (section 5), strict false-alarm accounting (section 7.4), on all 30 validation runs, giving `FA_strict` per evaluable host-day and `Recall_P` on validation. No candidate is skipped or treated specially; the no-alert candidate simply yields 0 alerts, `FA_strict = 0`, `Recall_P = 0`.
4. Feasible = `FA_strict <= B`, `B = 1` per evaluable host-day. **Choose the feasible candidate with the highest `Recall_P`; ties go to the highest `θ`.** Evaluate all candidates (the stateful dedup can make the relationship non-monotone); do not bisect.
5. Because the no-alert candidate is always feasible, **a threshold always exists**; the former "infeasible" outcome is replaced by an explicit report. **Whenever a frozen model entry selects the no-alert boundary, the report states it** (field `no_alert_selected: true` in `frozen_run.json` and the report, plus the count of seeds that did so for `logreg` and `xgboost`; the single `rule` evaluation is shown as "1 evaluation: yes/no"). That means no alerting rule reached any positive validation recall within the budget; the model is then evaluated on test **as a never-alerting system** (`Recall_P = 0`, zero alerts), with calibration and Brier still computed, and the result is not hidden or dropped. Selecting the all-alert boundary, or the lowest or highest quantile candidate, is flagged the same way through the single field `boundary_selected`, whose value is fixed by the precedence rule of section 8.2.
6. **Never** maximise F1, PR-AUC or any row-level metric to choose a threshold. Validation numbers at the chosen threshold are optimistic and are labelled "selected on validation".

**Primary model type** is named before the freeze, **from E1 validation results only (amendment A3)**: the model with the highest mean-over-seeds validation `Recall_P` at budget (ties: `rule`, then `logreg`, then `xgboost`). If every E1 model's mean validation `Recall_P` is 0 (all selected no-alert), **no primary model is named** and the report says so. E2 results never enter this choice. All models are reported on test regardless of that choice, with no omissions. The `rule` baseline (`max(cpu_mean_300, mem_last, disk_root_last, disk_data_last) / 100`) is a mandatory reference and goes through exactly the same selection.

### 8.1 No safety margin; generalisation check (amendment A6)

The budget `B = 1` is applied to validation exactly, with **no safety margin, no re-selection and no adjustment after any test number is seen**. The test result is the check on whether the validation-selected operating point generalises. For each model, seed and experiment the report prints, side by side, validation (labelled "selected on validation"), `test_iid`, `test_ood`, `ood_slow` and `ood_cache_node`: strict false alarms per evaluable host-day with 95 % interval, spec-style false alarms per evaluable host-day, `Recall_P`, `K / N` and alerts per host-day, plus the validation-to-test differences. **Budget verdict (amendment B1).** Assigned per split by this **ordered, mutually exclusive** rule, stopping at the first condition that holds; "lower 95 % bound" is the lower end of the run-level bootstrap percentile interval of the strict false-alarm rate (section 9):

1. lower 95 % bound `> 1`: **"budget exceeded with confidence"**;
2. otherwise, point estimate `> 1`: **"budget exceeded"**;
3. otherwise: **"budget reproduced"**.

Exactly one verdict results per split (a lower bound above 1 implies a point estimate above 1, so rule 1 is the stronger case of rule 2, and "budget exceeded" therefore means a point estimate above 1 with a lower bound of at most 1; a lower bound of exactly 1 does not trigger rule 1). None of these verdicts changes any threshold. A split where the budget is exceeded is reported as such, not repaired.

### 8.2 Boundary labels and the exact threshold record (amendment B5)

* **The exact threshold is preserved.** The selected `θ*` is stored as the exact float64 (decimal `repr` and IEEE hex, `float.hex()`) and is never rounded, snapped or replaced by a label.
* **Candidate-set hash.** The deduplicated, ascending candidate list is serialised as the concatenation of each candidate's 8-byte little-endian IEEE-754 float64 and hashed with SHA-256; the list length is recorded with it. Labelling `θ*` never changes the threshold, the list or the hash.
* **Labels `θ*` can equal** (exact float64 equality): `no_alert`: `θ* = nextafter(1.0, +inf)`; `all_alert`: `θ* = 0.0`; `lowest_quantile`: `θ*` equals the smallest quantile-derived candidate value (`θ_0`, tail mass 0.1); `highest_quantile`: `θ*` equals the largest (`θ_1999`, tail mass 1e-5). More than one can hold at once: `θ_all` equals `θ_0` when the 90th-percentile validation score is 0.0, and all quantile candidates coincide for a constant scorer.
* **Precedence.** `boundary_selected` takes **exactly one** value: `no_alert` > `all_alert` > `lowest_quantile` > `highest_quantile` > `none`. `no_alert` never co-occurs with another label (it exceeds 1.0). The informational list `boundary_labels_also_matching` records every lower-precedence label that also equals `θ*`; it is never used in a decision. `no_alert_selected` is true iff `boundary_selected == "no_alert"`.

## 9. Uncertainty

* **Run-level (cluster) bootstrap**: `B = 10,000` replicates, seed `20261201`, percentile 95 % intervals. Within each reported group resample whole runs with replacement, recompute every pooled metric from the stored per-run alerts and incident outcomes. Thresholds, models and calibrators are held fixed, so the interval reflects test-sample variability only.
* Paired replicates (same resampled runs) for model differences. OOD subgroups are resampled within the subgroup (10 runs each: intervals will be wide and are reported as such).
* **Training-seed variability**: `logreg` and `xgboost` are each trained with the five seeds `101` to `105`, each with its own threshold selection. Report per-seed results and the mean with min/max over those seeds. The best seed is never selected on val or test for the headline. The deterministic `rule` is evaluated exactly once (section 2.2) and carries no seed statistics.
* **Not estimable now**: generator-seed variability (one master seed exists), variation of the threshold-selection procedure itself.
* Indicative precision (independent-incident approximation, recall near 0.5): half-width about 0.085 for val (132 PRECURSOR), 0.069 for test_iid (201) and 0.093 for test_ood (111). The run-level bootstrap will be wider.

## 10. Expected counts per split (verified, from the audit and counts script)

PRECURSOR incidents by family (the denominators of per-family recall):

| Split | mem_leak | disk_fill | cpu_runaway | net_flood | pool_exhaustion | Flags |
|---|---|---|---|---|---|---|
| train | 100 | 100 | 83 | 72 | 100 | n/a (training) |
| val | 30 | 30 | 25 | 17 | 30 | cpu, net LOW-N |
| test_iid | 45 | 45 | 37 | 29 | 45 | net LOW-N |
| test_ood | 25 | 25 | 19 | 17 | 25 | all LOW-N |
| ood_slow | 12 | 17 | 12 | 9 | 15 | all LOW-N, net count only |
| ood_cache_node | 13 | 8 | 7 | 8 | 10 | disk, cpu, net count only; rest LOW-N |

ABRUPT incidents are net_flood and cpu_runaway only (train 28 + 17, val 13 + 5, iid 16 + 8, ood 8 + 6); every SILENT incident is `silent_crash`. Evaluable decision points: train 1,774,885; val 591,855; test_iid 788,765; test_ood 393,391. Eligible decision points in a PRECURSOR credit window: 28 for most incidents (range 2 to 28). PRECURSOR incidents without any eligible decision: 0 in every split.

## 11. Freeze, test isolation and execution order

1. Fix and archive this protocol (SHA-256 of the MD and JSON).
2. Train `logreg` and `xgboost` on train with each of the five seeds, for E1 (default view) and E2 (operational target). `rule` has no training and is evaluated once, in E1 only (section 2.2).
3. Select thresholds on val for every model, seed and experiment (candidate set of section 8, boundaries included); derive the reliability-bin boundaries from validation scores (section 7.5); fit the isotonic calibrators on val; **name the primary model type from E1 validation only**.
4. **Freeze**: write `frozen_run.json` containing the protocol hashes, dataset/labels/features manifest hashes, `requirements.lock` hash, git commit, model artifact SHA-256 values, **21 model entries** (section 2.2: `rule` once with `training_seed: null`, plus 5 seeds each of `logreg` and `xgboost` for E1 and for E2), each with the exact threshold (`repr` and hex), the candidate-set hash (section 8.2) and length, the single-valued `boundary_selected` with `boundary_labels_also_matching`, `no_alert_selected`, the reliability-bin boundaries and their actual bin count, the isotonic calibrator, the chance-diagnostic offsets seed, validation metrics, training seeds, primary model type (or "none") and the time of freezing, and record its own SHA-256. It must exist before any test file is opened. Nothing in it may be derived from test data.
5. Run the evaluator once on `test_iid` and `test_ood` (and their subgroups) for E1 and E2, including the chance diagnostic of section 7.9. A **split-access log** is written with every report; reading a test file before step 4 is a violation. A **poison-file test** (spec section 16) replaces test files with files that raise on read and runs steps 2 and 3 to completion.
6. Anything changed after test numbers were seen is marked **post-hoc** in the report and never appears in a headline.
7. Reports carry the tier labels "Synthetic IID", "Synthetic OOD (limited initial robustness test)" and "Real-world operational validity: not established".

## 12. Report format and amendments

* Output: one JSON report (schema in the JSON twin) and one Markdown report. Section order: opening sentence, frozen-run summary (including every `no_alert_selected` / `boundary_selected` flag and the actual reliability bin counts), accounting block, headline table for **E1** (`Recall_P`, `K/N`, strict false alarms per evaluable host-day with the spec-style rate in its own column, precision, with 95 % intervals), operating-point generalisation table (section 8.1), per-family table, lead and delay tables, calibration (frozen bins), ABRUPT/SILENT chance diagnostic (own section, labelled diagnostic), exclusion and exposure breakdown, **secondary experiment E2 (own section, never in the headline)**, limitations.
* Headline table is shown for validation (labelled "selected on validation"), `test_iid` and `test_ood` (with its two subgroups), each separately.
* Forbidden wording: "deployed false-alarm rate", "production false-alarm rate", "alert suppression during incidents works", "predicts outages" without the section 13 qualifiers, and any statement that the oracle mask is a feature of the system.
* Amendments (new models, features, horizons, bins, thresholds or budget) require a protocol version bump and a re-run from step 2; results from different protocol versions are not compared.

## 13. Limitations that must accompany every result

1. **One master seed** (seed 0) exists; seed 1 is partial and unusable. Generator-seed variance is unknown.
2. **OOD coverage is weak**: half the OOD runs are statistically identical to train; the rest only slow the ramps by 2.5x. No new mechanism, threshold distribution, cadence or noise regime is tested.
3. **30.8 % of incidents (401 of 1,300) are unpredictable by design** (300 SILENT, 101 ABRUPT). The ceiling for the predicted share of all incidents is about 0.69.
4. The target is a **simulator-defined saturation event** (utilisation reaching a threshold), not a validated real-world outage. Real-world operational validity is not established.
5. **Some positive windows precede any visible precursor**: 12.3 % of positive rows overall, 56.5 % for net_flood. Part of the miss rate is irreducible.
6. False-alarm figures are conditional on the oracle active-incident mask and the data-sufficiency exclusion; telemetry-loss periods (about 1 % of decision points) are unmeasured.
7. Run-level separation is not scenario-level generalisation; all splits share the same families, shapes and thresholds.
8. Row-level metrics (Brier, reliability) use strongly autocorrelated rows (about 30 near-identical positive rows per incident); only the bootstrap by run is valid for them.

## 14. Where this protocol differs from spec v4.5.1 (for your review)

* **D1.** Spec section 14.3 marks ABRUPT/SILENT incidents "detected" by stage-2 leftover alerts. Here they are **never detected**; stage 2 is a labelled diagnostic (your decision 6). **Approved.**
* **D2.** Spec fa-2 excludes "unattributed" alerts (those before ABRUPT/SILENT incidents) from the false-alarm numerator. Here they count as **strict false alarms** for the budget, and the spec-style rate is also reported in its own column. **Approved (amendment A5): the strict definition stays.** Effect is small (unscorable rows are 0.3 % of evaluable rows).
* **D3.** PRECURSOR incidents with no evaluable decision count as missed (spec: reported separately). Currently 0 cases.
* **D4.** Brier, calibration, detection delay and the lead-conditional recalls are new.
* **D5.** Threshold selection fixes the candidate set (quantile grid plus explicit no-alert and all-alert boundaries) and the tie-breaks (the spec leaves them open).
* **D6.** The operational-target training view is a separately reported secondary experiment E2 (the spec lists it as required); it never enters the headline or model selection.
* **D7.** The ABRUPT/SILENT chance baseline is mandatory in the first evaluation, strictly diagnostic.
* **D8.** No validation safety margin (section 8.1).

## 15. Consistency with the audit

| Audit finding | Protocol clause |
|---|---|
| Labels, horizon and as-of features verified exact | sections 1, 3 (labels used as stored) |
| 30.8 % unpredictable by design; dropping them hid the ceiling | 6, 7.2, 13.3 |
| Unscorable alerts counted as correct; denominator included unscorable rows | 5, 7.4, D2 |
| Saved results from 2 train and 1 val run; chance-level; overfit | 2, 8, 9, 11 |
| Threshold was max F1, not budget | 8 |
| No incident recall, lead time or calibration | 7.1, 7.6, 7.7, 7.5 |
| One seed, no repeats | 9 (five training seeds), 13.1 |
| Missing-as-zero latent defect | 3 pre-flight guard |
| OOD half label-only | 2, 9, 13.2 |
| Single master seed; code-hash mismatch | 11 (hashes recorded), 13.1 |
| Positive windows before any precursor; weakly visible precursors | 7.7, 13.5 |
| Rows are near-duplicates; effective sample is the incident count | 1, 9, 13.8 |
| (A1) A threshold must always exist and a model that cannot meet the budget must be visible | 8 steps 2 and 5: no-alert and all-alert boundaries, `no_alert_selected` reporting |
| (A2) Calibration needs bins that do not depend on test data | 7.5: validation-derived, frozen, deterministic on ties |
| (A3) Unscorable windows must not steer the primary result | 2.1: E2 separate, never used for selection |
| (A4) ABRUPT/SILENT coincidence needs a chance reference, not credit | 7.9: 1,000 shifts, diagnostic only |
| (A5) The audit found unscorable alerts flattered the old accounting | 5, 7.4, D2: strict stays, spec-style rate separate |
| (A6) The test must show generalisation of the validation operating point | 8.1: no margin, mechanical verdict wording |
| (B1) Verdict conditions overlapped | 8.1: ordered, mutually exclusive rule |
| (B2) Fake seed variability for a deterministic baseline | 2.2, 9, 11: `rule` evaluated once, seeds only for logreg/xgboost |
| (B3) Precision is undefined without alerts; silent substitution would mislead | 7.3: `N/A (zero alerts)`, probability metrics still computed |
| (B4) Dedup of a shifted alert sequence depended on an arbitrary linear cut | 7.9: circular chain dedup, rotation-equivariant |
| (B5) A threshold can equal several boundary labels | 8.2: single-valued `boundary_selected` by precedence, exact threshold and hash preserved |
| (B6) The basis spec was only in a scratchpad | `docs/SIMULATOR_SPEC_V4_5_1_SECTIONS_13_16.md`: verbatim, hashed |

## 16. Unresolved ambiguities

Resolved by your reviews and no longer open: strict false-alarm definition (A5), the 1,000-shift diagnostic (A4), the operational-target view (A3), the safety margin (A6: none), no-alert models evaluated on test as never-alerting systems (confirmed), `rule` excluded from E2 (confirmed), and the spec provenance (B6: verbatim extract with hashes). Remaining:

1. Detection delay depends on the latent `precursor_start`, which is simulator ground truth, not observable.
2. Exposure and alert timing use the 60 s decision grid; sub-minute timing cannot be resolved.
3. Numerical choices that are mine and can change before the freeze: bootstrap `B = 10,000` and seed `20261201`, chance-diagnostic seed `20261202`, training seeds `101` to `105`, and the interior reliability quantile levels {50, 80, 90, 95, 98, 99, 99.5, 99.9} %.
4. A heavily tied scorer (for example `rule`) yields fewer reliability bins because tied boundaries merge; this is intended and reported, but it makes that model's calibration table coarser.
5. The extract preserves only sections 13 to 16 of the simulator spec. The constants and definitions they cite (sections 1, 2, 4.5, 5 to 8, 12, 23) remain only in the scratchpad source; the protocol restates every value it needs (horizon, lead, dedup, exposure) and the dataset's own `protocol.json` carries them, but a reader cannot re-derive the spec's other sections from the repository.
6. The deterministic `rule` uses chance-diagnostic stream id 0, so its shifts differ from the seeded models' shifts; this is stated and affects only descriptive rule-versus-model comparisons in that diagnostic.

## 17. Amendment log (v1.0 to v1.2, reviews of 2026-11-19)

v1.0 hashes: `docs/ML_EVALUATION_PROTOCOL.md` `004c75f27d521c6b19a602c9fb35106fc32d15802e8d61f462954710158f0b4c`; `docs/evidence/ml_evaluation_protocol.json` `547e63aa34efed6a11773bb0fb6935cd8539ea4444bde9e1ecef4e5987e196f1`. The expected-counts file is unchanged: `5fe0d63708d9d64cb56ba3164989d595be440720d58c265568b094c2c5c907c9`.

| # | Amendment | Where |
|---|---|---|
| A1 | Candidate set = existing 2,000 quantile candidates + explicit no-alert boundary (`nextafter(1.0, +inf)`) + all-alert boundary (`0.0`), deduplicated, all evaluated through the identical pipeline; no-alert selection is reported; "infeasible" outcome replaced; scores asserted in `[0, 1]`; "no primary model" rule when all E1 recall is 0 | sections 4, 8 |
| A2 | Reliability bins derived from validation scores only, frozen before test, shared by IID/OOD/subgroups, ties merged deterministically (boundaries equal to the minimum validation score discarded so no bin is empty), actual bin count reported | 7.5, 11 |
| A3 | Operational-target view is secondary experiment E2, separately reported, never used for primary-model selection; PRECURSOR-only E1 stays primary | 2, 2.1, 8, 11, 12 |
| A4 | 1,000 seeded circular-shift ABRUPT/SILENT baseline is mandatory in the first evaluation; fully specified; strictly diagnostic | 7.9, 11 |
| A5 | Strict false-alarm definition kept; spec-style rate in its own column | 5, 7.4, 14 |
| A6 | No validation safety margin; mechanical generalisation verdicts | 8.1 |

Nothing else changed between v1.0 and v1.1: splits, expected counts, alert emission, matching, outcome classes, exposure, metrics other than the above, bootstrap and the limitations.

### v1.1 to v1.2 (final review, 2026-11-19)

v1.1 hashes: `docs/ML_EVALUATION_PROTOCOL.md` `cd0171a8fe43c4615b437799f905120c4fb1fd7008c3d9ca2c4b3d2abbdefe05`; `docs/evidence/ml_evaluation_protocol.json` `bd14ab2a402cdabbe98686d88b2fc32e32e687f30ab09c4b79e6cd6fcca5689a`. The expected-counts file is still unchanged (hash above); splits, counts, matching, exposure, metrics definitions other than those below, bootstrap and limitations are unchanged.

| # | Amendment | Where |
|---|---|---|
| B1 | Budget verdict is one ordered, mutually exclusive rule: lower 95 % bound > 1 "budget exceeded with confidence"; else point estimate > 1 "budget exceeded"; else "budget reproduced" | 8.1, 7.10 |
| B2 | `rule` is deterministic: evaluated exactly once, E1 only; five seeds apply only to `logreg` and `xgboost`; defined representation in `frozen_run.json` (21 entries), threshold selection, bins, chance diagnostic (stream id 0) and summaries | 2.2, 2.1, 7.5, 7.9, 8, 9, 11 |
| B3 | Precision is `N/A (zero alerts)` when a model emits no alerts; alerts and `Recall_P` stay 0; probability metrics still computed; defined aggregation and bootstrap handling | 7.3 |
| B4 | Circular-shift dedup is defined on the circle (chains cut at gaps >= 15 steps), rotation-equivariant, independent of the linear cut; same 900 s interval and exclusion handling | 7.9 |
| B5 | `boundary_selected` is single-valued by precedence `no_alert` > `all_alert` > `lowest_quantile` > `highest_quantile` > `none`; exact threshold (`repr` and hex) and candidate-set hash (SHA-256 of float64 little-endian bytes) preserved | 8.2, 11 |
| B6 | Sections 13 to 16 of the simulator spec v4.5.1 copied verbatim with source, version and hashes (source SHA-256 `a1829d0396f0a833507194e0beafa87b65204a110e93cbf068fded7674f5bf09`, extract `3a1c1524905f0336f48a1358388dbb97cf7baa1661c3edd726cce8055e0f2fee`) | `docs/SIMULATOR_SPEC_V4_5_1_SECTIONS_13_16.md` |