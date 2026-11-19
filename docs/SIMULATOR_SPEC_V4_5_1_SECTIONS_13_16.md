# Simulator specification v4.5.1, sections 13 to 16 (verbatim extract)

**Provenance record** (written by the extractor on 2026-11-19; it is the only text in this file that is not source text).

| Item | Value |
|---|---|
| Document | "UptimeTrace Milestone 3.0: simulator v2 and ground-truth specification (v4.5.1)" |
| Version stated by the source | v4.5.1 (its first line); the source says `protocol_version` stays "4.5" and that it "does not declare M3.0 approved" |
| Source file | `UptimeTrace_M3.0_spec_v4.5.md` in an earlier session scratchpad: `D:\Temp\claude\8c0b31017890487f\dc2cda8f-8002-43f7-bb38-2cbcda9e9da8\scratchpad\` |
| Source size / modified | 147,958 bytes; 2026-11-12 07:44:37 local time; 1,387 lines, LF line endings, no BOM |
| Source SHA-256 | `a1829d0396f0a833507194e0beafa87b65204a110e93cbf068fded7674f5bf09` |
| Sibling rendering (not used) | `UptimeTrace_M3.0_spec_v4.5.html`, SHA-256 `fd8a3c81b0fdac1c7a5aa68680d10f430079e90a516941b1755f8f20ef101f29`, written 5 s after the `.md`, so the `.md` is the source |
| Extracted | source lines **985 to 1162** inclusive: from the heading "## 13. Label precedence" to the last line of section 16. Section 17 starts at line 1164; blank line 1163 is not included |
| Extracted size / SHA-256 | 23,492 bytes; `3a1c1524905f0336f48a1358388dbb97cf7baa1661c3edd726cce8055e0f2fee` |
| Method | byte-for-byte slice of whole LF-terminated lines. No edit, reflow, re-encoding, re-numbering or added text between the two marker lines. Verified after writing by re-reading this file and comparing the bytes between the markers with the source slice |
| Not included | every other section. Sections 13 to 16 refer to constants and definitions in sections 1, 2, 4.5, 5, 6, 7, 8, 12 and 23 and to tags [D1] to [H2]; those sections exist only in the scratchpad source and are **not** reproduced here |
| Used by | `docs/ML_EVALUATION_PROTOCOL.md` (matching = section 14.2 and 14.3, exposure = section 14.4, labels = section 13, claims = section 16) |

If the source file is lost, this extract (and its recorded hashes) is the durable copy of these four sections; the rest of the specification would then be unavailable.

<!-- BEGIN VERBATIM SOURCE LINES 985-1162 -->
## 13. Label precedence

For host h at decision time t:

1. excluded_insufficient_data (section 12.6, whole decision point).
2. excluded_active (oracle active-incident mask) [G4]: t ∈ [onset_j, oracle_mask_end_j) for any merged incident j, where oracle_mask_end_j = max(cooldown_end_j, max over the incident's members of conflict_end_m). The exclusion is the union over incidents.
3. O = {j : t < onset_j ≤ t + H}.
4. positive: some j ∈ O has group PRECURSOR.
5. unscorable: O is non-empty but contains no PRECURSOR.
6. negative: O is empty.

**Views.**

| View | positive | negative | unscorable |
|---|---|---|---|
| Training (default) | 1 | 0 | masked from the loss |
| Training ablation (required): "operational target" | 1 | 0 | 1 |
| Evaluation (primary) | scored | scored | masked |
| Evaluation (secondary, "all incidents") | 1 | 0 | 1 |

All excluded_* decision points are dropped in every view.

**Oracle mask end [G4].** The oracle mask must not end while any incident member's fault contribution is still returning to baseline.

- cooldown_end = recovery + 900 s is unchanged. It continues to define incident merging (cooldown = merge window) and the `cooldown_end` field, and is the lower bound of the mask end. No second cooldown is introduced, and the return ramp is not extended by an additional 900 s.
- conflict_end_m = impact_end_m + T_ret_m (section 4.5) is the instant at which member m's contribution reaches exactly zero. Members are the incident faults of the merged incident (primary and relapse). Twins, benign episodes and faults of other incidents are not members.
- oracle_mask_end = max(cooldown_end, max conflict_end_m). Because T_ret ≤ 1200 s and the cooldown is 900 s, the mask end exceeds cooldown_end by up to 300 s for a member whose ramp outlasts the cooldown, and equals cooldown_end otherwise. A relapse's own return ramp counts, and the maximum is over all members, not just the primary.
- Merging, group recovery, cooldown_end, onsets, labels (steps 3–6) and alert dedup do not depend on conflict_end and are unchanged. Only step 2 and the quantities derived from it (decision-point eligibility, label and evaluation exclusion, evaluable exposure) change.
- Planner consistency: the planner rejects any group with max(R+900, max conflict_end) > run_end, which is exactly oracle_mask_end > run_end, so the mask never extends past the run (section 5). Masks of consecutive planned incidents are disjoint.

**Nature of the oracle mask [D6, G4].**

- excluded_active is an oracle evaluation mask. It is computed from simulator ground truth (incident onsets, recoveries and fault return-ramp ends, i.e. latent parameters T_ret) that no deployed system can observe.
- It is retained because it keeps the benchmark from scoring decision points during and immediately after an incident, whose telemetry is dominated by the incident itself, including the period in which the fault's effect is still decaying to baseline.
- It is not a deployable alert-suppression mechanism, and the benchmark provides no evidence that such a mechanism would work. In particular, a production system does not know when a fault's return ramp ends; the return-ramp end exists only in the simulator's ground truth.
- The 900 s mask lower bound (cooldown) is an incident-structure constant. It is distinct from alert_dedup (an alert policy) and from any production suppression.
- A real deployment that suppresses alerts during incidents must use independently observable incident state (for example an acknowledged page, a ticket, or health checks). This benchmark does not evaluate any such mechanism.
- Every false-alarm figure in this benchmark is conditional on this mask and on the data-sufficiency exclusion, and must be labelled so.
- Label precedence is unchanged apart from the mask end. The identifier excluded_active is kept for compatibility. Reports and documentation say "oracle active-incident mask".
- FR-3 (future, not M3.1–M3.5): an evaluation without the oracle mask, either counting alerts during incidents as a separate category or using a mask derived only from observable signals.

**Interaction with merged incidents.** One onset per merged incident, classified by its primary member. A relapse never creates a second onset. An ABRUPT primary with a PRECURSOR relapse is ABRUPT; a PRECURSOR primary with a SILENT relapse is PRECURSOR. The group is independent of H.

**Worked example.** At t, an ABRUPT onset lies at t+10 min and a PRECURSOR onset at t+25 min: the decision point is positive at H = 30 min and unscorable at H = 15 min.

**Worked example, mask end [G4].** Incident with onset 10:00, recovery 10:05, so cooldown_end = 10:20. Single member with T_ret = 18 min: conflict_end = 10:23, oracle_mask_end = 10:23 (source: return_ramp). A decision at 10:21 is excluded_active (under v4.4 it would have been scored). With T_ret = 10 min: conflict_end = 10:15, oracle_mask_end = 10:20 = cooldown_end (source: cooldown); the decision at 10:21 is scored.

**Tests.** Truth tables for all group combinations; boundary tests (onset at t is excluded_active, at t+H is inside, at t+H+ε is outside); and monotonicity: for non-excluded decision points, negative < unscorable < positive can only increase as H grows. [G4] Mask-end tests: a decision at oracle_mask_end − ε is excluded and one at exactly oracle_mask_end is not (right-open); with conflict_end > cooldown_end, decisions in [cooldown_end, conflict_end) are excluded_active and source = return_ramp; with conflict_end ≤ cooldown_end the mask end equals cooldown_end (ties give source = cooldown); a relapse member whose ramp is later than the primary's determines the end; the maximum is taken over incident members only (a twin's conflict_end never enters); mutating T_ret changes oracle_mask_end but leaves onset, recovery, cooldown_end, members, group and the merge result unchanged; the exclusion is the union when two synthetic incidents' masks overlap.

## 14. Event-level alert matching (match-1) and false-alarm exposure (fa-2)

### 14.1 Alert emission

Per host and per H, using a frozen operating threshold. Scores are computed only at decision times that are not excluded_*. An alert cannot be emitted at a masked decision point; this is a property of the benchmark, not of any deployable system. The first decision time with score ≥ threshold emits an alert, which suppresses candidates with t < t0 + 900 s. Suppressed crossings emit nothing, are not alerts, start no window, and receive no credit; their count is reported. A consequence is that dedup can cause a miss when a suppressed crossing falls inside a credit window.

### 14.2 Credit window and matching

**Credit window.** Alert t is eligible for merged incident o iff o − H ≤ t ≤ o − L_min (closed). The credit window is the positive window [o−H, o) minus its final 120 s.

**Stage 1: PRECURSOR matching.** Process PRECURSOR incidents in increasing onset. Each takes the earliest still-unconsumed eligible alert, which is then consumed. This is optimal for equal-length windows, and a property test compares it with exhaustive maximum matching on random small cases. One alert credits at most one incident, and one incident is detected at most once.

**Stage 2: ABRUPT and SILENT matching.** The same greedy runs over the remaining unconsumed alerts. PRECURSOR therefore has priority when both fall within an alert's horizon.

### 14.3 Alert outcomes and incident metrics

Each alert gets exactly one outcome:

- hit: stage-1 credit.
- duplicate: not credited, but inside the credit window of a PRECURSOR incident already credited by another alert.
- unattributed: otherwise, any merged incident (any group, including a PRECURSOR onset under 120 s away) has onset in (t, t+H]. This includes stage-2 matches.
- false_alarm: otherwise.

Incident outcomes. PRECURSOR: detected iff stage-1 matched, with lead = o − credited alert time. ABRUPT/SILENT: detected iff stage-2 matched.

Precision. Strict = hits ÷ all alerts; lenient = (hits + duplicates + unattributed) ÷ all alerts. Chance baseline for ABRUPT/SILENT: circularly shift the alert timeline within each run by a seeded random offset (1,000 shifts), re-apply dedup and exclusions, re-match, and report observed minus mean shifted recall with an empirical one-sided p-value.

**Worked examples** (H = 30 min, L_min = 2 min):

- **Overlapping windows.** P1 onset 10:00, recovering by 10:05; P2 onset 10:25. Windows are [09:30, 09:58] and [09:55, 10:23]. One alert at 09:56 credits P1 only and P2 is missed. Adding an alert at 10:21 credits P2, valid when P1's oracle mask has ended by 10:21: P1's cooldown ends at 10:20, and its return ramp ends by 10:15 (T_ret = 10 min), so oracle_mask_end = 10:20. [G4] (With T_ret = 18 min the mask would run to 10:23 and no alert could be emitted at 10:21.)
- **Mixed groups.** PRECURSOR onset 12:00, SILENT onset 12:20. A single alert at 11:55 lies in both credit windows. It credits the PRECURSOR incident (hit) and the SILENT one stays undetected.
- **Boundaries.** An alert at exactly o−H credits; at exactly o−120 s credits (lead 120 s); one second later does not and is unattributed. An alert at t0 + 900 s fires and one at t0 + 899 s is suppressed. An alert at exactly o is in excluded_active and ignored.

### 14.4 False-alarm exposure and metrics (fa-2) [D2, D4, D6, G3, G4]

**Why two denominators.** An excluded decision point cannot emit an alert, so a host with telemetry gaps has less time in which it could raise a false alarm. Dividing by calendar time would make such a host look quieter than a continuously monitored one.

**Conditional on the oracle mask [D6, G4].** All figures below are conditional on the data-sufficiency exclusion and the oracle active-incident mask, whose per-incident span is [onset, oracle_mask_end) (section 13). They are not estimates of the false-alarm rate of a deployed system.

**Partition of the run [D4].** For a host with run [run_start, run_end) and decision interval Δ:

- Boundaries: B_j = run_start + j·Δ, for j = 0, 1, …, J with J = ceil((run_end − run_start)/Δ) − 1.
- Intervals: I_j = [B_j, min(B_j + Δ, run_end)). They are pairwise disjoint and their union is exactly [run_start, run_end), so Σ_j |I_j| = run_end − run_start.
- Decision time of interval j: t_j = run_start + decision_offset + j·Δ, with the constraint 0 ≤ decision_offset < Δ. This is the instant at which the decision about I_j is made, using data available at t_j. An alert emitted at t_j is attributed to I_j. [G6] The exposure arithmetic needs only this constraint. The stricter decision_offset > 3.9 s applies to feature-building configurations (section 2), not to arithmetic-only exposure fixtures.
- Initial partial interval: none (length 0). I_0 starts at run_start. The decision_offset is the lag between an interval's start and its decision time, and leaves no uncovered time. (v4.2 started the first interval at the first decision time, which left [run_start, run_start + offset) uncovered, so a fully evaluable 24-hour run totalled 86,395 s instead of 86,400 s.)
- Subsequent intervals: full length Δ.
- Final clipped interval: if the run length is not a multiple of Δ, I_J = [B_J, run_end) has length run_end − B_J < Δ.
- [G5] B_j is anchored at run_start, the same reference as the sampling origin and the gap/partition grid (section 8.2), so when Δ is a multiple of cadence_s, each decision time is exactly decision_offset after a nominal tick.

**Evaluability status of I_j.** It is evaluable iff both hold: (i) t_j < run_end (a decision is actually made inside the run) and (ii) decision point j is neither excluded_insufficient_data nor excluded_active, each evaluated at the instant t_j. Otherwise its status is exactly one of the following, in this precedence: excluded_insufficient_data, excluded_active, no_decision (when t_j ≥ run_end, which can only affect the final clipped interval).

The metric is decision-quantised, not actual-time. An interval counts wholly or not at all according to the status of its single decision instant. There is no pro-rating.

An exclusion span [s, e) (half-open) excludes decision j iff s ≤ t_j < e. For the oracle mask, s = onset and e = oracle_mask_end of the incident. [G4]

- A span beginning at s inside I_j: if s ≤ t_j, I_j is wholly uncounted, including the part before s. If s > t_j, I_j is wholly counted, and I_{j+1} is the first uncounted interval.
- A span ending at e inside I_j: if e ≤ t_j, I_j is wholly counted, including the part before e. Otherwise it is wholly uncounted.
- The difference between the time removed from exposure and the actual masked time is less than Δ at each span boundary, and cancels when the span is aligned to the decision grid.

**What is not counted.** Time attributed to a decision that cannot emit an alert because of the evaluation protocol (an excluded decision, or no decision at all) is not counted. Exposure counts decision opportunities times Δ. It does not claim that an alert could be emitted at every instant of an evaluable interval, since alerts occur only at decision instants.

**Dedup suppression is counted as exposure [G3].** The 900 s suppression window (section 14.1) is part of the alerting policy being evaluated, not a data or oracle condition, so decision intervals suppressed by dedup stay evaluable and stay in E_h. A suppressed threshold crossing is not an alert: it is never classified false_alarm and is not in N_FA. Only emitted alerts are counted in the false-alarm numerator.

*Rationale.* With suppressed intervals kept, E_h depends only on the run bounds, Δ, decision_offset and the two exclusions (data sufficiency and the oracle mask). It never depends on the model, the threshold or the alerts, so every threshold and model is scored against the same denominator and the budget constraint compares like with like. The benefit of dedup already reaches the metric through the numerator: suppressed crossings are not counted. Removing suppressed intervals from exposure as well would make the denominator shrink as a model alerts more, which would raise its false-alarm rate per unit of exposure and make the rate depend on how the model's own alerts cluster. That is why suppressed time is not removed. This choice is approved (section 23, item 3).

**Definitions.**

- Evaluable exposure E_h = Σ_{evaluable j} |I_j| in seconds, converted to 24-hour equivalents: evaluable_host_days = E_h / 86,400.
- Calendar exposure C_h = (run_end − run_start) / 86,400 (14 for every run in the primary dataset).
- N_FA is the number of emitted alerts classified false_alarm under the frozen threshold and horizon. Every counted alert lies in a counted interval, because alerts are emitted only at evaluable decision instants (a test asserts this).

**Metrics** (pooled over a split as ratios of sums):

- Calendar false alarms per host-day = N_FA / Σ C_h.
- Evaluable false alarms per evaluable host-day = N_FA / Σ (E_h / 86,400).
- Also reported: the exposure fraction φ = Σ E_h / Σ (C_h · 86,400), the breakdown of non-evaluable time by status (excluded_insufficient_data, excluded_active, no_decision), and per-host distributions (median, 90th percentile, maximum; hosts with E_h = 0 are N/A). Both metrics get cluster-bootstrap intervals by run.

**Relationship.** Since E_h ≤ C_h, φ ≤ 1 and evaluable rate = calendar rate / φ ≥ calendar rate. Exposure does not depend on H.

**Which metric constrains the operating point.** Validation operating-point selection maximises PRECURSOR event recall subject to the evaluable false-alarm rate on val being ≤ the budget B (default 1 per evaluable host-day). Because the evaluable rate is never lower than the calendar rate, this also guarantees the calendar rate is ≤ B. The calendar rate is reported but never used for selection. If no threshold meets the budget, the result is reported as infeasible.

**Worked example (24 calendar hours, 12 evaluable hours).** One host over a run of 86,400 s, Δ = 60 s, decision_offset = 5 s.

- Partition: I_j = [60j, 60j+60) for j = 0 … 1439, 1,440 intervals, no initial partial interval and no clipped final interval. Decision times are t_j = 60j + 5.
- Fully evaluable host: all 1,440 intervals are evaluable, so E = 1,440 × 60 = 86,400 s = 1.0 evaluable host-day, exactly.
- Example host. An oracle mask spans [21,600 s, 43,200 s) (for example an incident with onset 21,600 s and oracle_mask_end 43,200 s; the arithmetic does not depend on whether the end is the cooldown end or a return-ramp end). It excludes decisions with 21,600 ≤ 60j+5 < 43,200, i.e. j = 360 … 719 (t_360 = 21,605, t_719 = 43,145; t_359 = 21,545 and t_720 = 43,205 are outside): 360 decisions, 21,600 s. A data outage excludes decisions j = 900 … 1259: 360 decisions, 21,600 s. In total 720 intervals are excluded.
- E = 720 × 60 s = 43,200 s = 12 h = 0.5 evaluable host-days.
- It raises 3 false alarms. Calendar rate = 3 / 1 = 3.0 per host-day. Evaluable rate = 3 / 0.5 = 6.0 per evaluable host-day.
- A continuously monitored host with 24 evaluable hours and 3 false alarms has 3.0 and 3.0. With B = 1, both exceed the budget, but the gappy host's alarm rate per monitored time is twice as high.
- Pooled over the two hosts: N_FA = 6; calendar = 6 / 2 = 3.0; evaluable = 6 / (0.5 + 1.0) = 4.0; φ = 36 h / 48 h = 0.75.
- Quantisation illustration. If the oracle mask were [21,606 s, 43,206 s), it would exclude j = 361 … 720 (t_360 = 21,605 < 21,606, t_720 = 43,205 < 43,206). Uncounted time is [21,660, 43,260) = 21,600 s, equal to the mask length, but shifted by 54 s: I_360 = [21,600, 21,660) is counted although its last 54 s are masked, and I_720 = [43,200, 43,260) is uncounted although its last 54 s (after the mask ends at 43,206) are unmasked. The two errors cancel here; in general each boundary contributes an error below Δ.

**Boundary tests.**

- (a) Full evaluability: the 24-hour host with every interval evaluable has E = 86,400 s exactly (also with decision_offset = 0 and decision_offset = 59; arithmetic-only fixtures, section 2 [G6]).
- (b) The worked example reproduces exactly: 720 excluded, E = 43,200 s, 3.0 vs 6.0, pooled 3.0 vs 4.0, φ = 0.75.
- (c) Partition property: for random run lengths and offsets in [0, Δ), the intervals are pairwise disjoint, their union equals [run_start, run_end), and Σ|I_j| = run length.
- (d) Final clipped interval: run length 86,430 s gives I_1440 = [86,400, 86,430) of length 30 s with t_1440 = 86,405 < run_end, so a fully evaluable host has E = 86,430 s. Run length 86,403 s gives a 3 s final interval with t_1440 = 86,405 ≥ run_end: status no_decision, uncounted, so E = 86,400 s. Run length 86,405 s: t_1440 = run_end, so no_decision, strict inequality.
- (e) Span start: a mask starting exactly at t_j excludes decision j; starting one second later leaves j counted and excludes j+1.
- (f) Span end: a mask ending exactly at t_j leaves j counted (half-open); ending one second later excludes j.
- (g) Both reasons: an interval hit by both excluded_insufficient_data and excluded_active is reported under excluded_insufficient_data only, and the three non-evaluable categories sum to C − E.
- (h) A host with zero evaluable exposure is N/A and adds 0 to numerator and denominator.
- (i) Evaluable rate ≥ calendar rate for every split.
- (j) Exposure is identical across the three horizons.
- (k) Active-incident exclusion lowers E_h but not C_h. [G4] A decision with cooldown_end ≤ t_j < oracle_mask_end is non-evaluable (excluded_active) and one at t_j = oracle_mask_end is evaluable, so with conflict_end > cooldown_end the evaluable exposure is lower than under a cooldown-only mask by exactly the number of decisions in [cooldown_end, oracle_mask_end) times Δ.
- (l) [G3] Dedup-suppressed intervals remain evaluable; E_h is identical for any two thresholds or alert sets on the same data; suppressed crossings are absent from N_FA.
- (m) Every alert counted in N_FA lies in an evaluable interval.
- (n) A configuration with decision_offset ≥ Δ is rejected.

## 15. Reporting and low-count handling

- The manifest and every report list actual counts per split × family × group (PRECURSOR/ABRUPT/SILENT) and per silent_reason, with sub_noise shares per family.
- A cell with fewer than 30 incidents is flagged LOW-N. A cell with fewer than 10 reports counts only, with no estimate.
- Every per-family estimate is exploratory and must carry a cluster-bootstrap interval by run, even when not LOW-N.
- Quotas are not increased to improve recall estimates. A later top-up would require a separate decision and a new dataset id.
- Every evaluation reports both false-alarm metrics, the exposure fraction and its per-status breakdown, per split and horizon. [D6] Every false-alarm table, figure and sentence carries the label "conditional on the oracle active-incident mask and the data-sufficiency exclusion", and reports state the share of calendar time under each mask separately.
- Reports also give the per-split exclusion rate by reason code (section 12.6), unevaluable PRECURSOR counts, and a diagnostic flag for any host with over 50% of decision points excluded (class P, provisional).
- Known effect: about 40% of cpu_runaway, the step share of net_flood, and sub_noise cases will reduce PRECURSOR counts below the quota values.

## 16. Evaluation and claims policy

- **Splits.** Cross-validation uses train only. val is used for tuning and operating-point selection.
- **Operating point.** Chosen on val to maximise PRECURSOR event recall subject to the evaluable clean-false-alarm rate (conditional on the oracle mask) ≤ B (default 1 per evaluable host-day), then frozen.
- **Test isolation.** test_iid and test_ood are unreachable from feature selection, threshold tuning, model selection and early stopping. A poison-file test replaces test files with files that raise on read and runs the whole train/tune/select pipeline to completion. A split-access log is written with each report. Anything changed after test or OOD numbers were seen is marked post-hoc.
- **OOD.** test_ood is a limited initial synthetic robustness test with two shifts. It is not evidence of general robustness.
- **Metrics.** Decision-point-level PR-AUC (primary uses the evaluation view with unscorable masked, secondary is all-incidents, prevalence always shown). Event-level recall per group at the frozen operating point, with lead time and lead buckets (<5, 5–15, 15–30, ≥30 min) for PRECURSOR. Recall excess over chance for ABRUPT and SILENT. Both false-alarm metrics. Poor SILENT recall is never pooled with PRECURSOR recall.
- **Tier labels.** Every table, figure and sentence carries one of: Synthetic IID, Synthetic OOD (limited initial robustness test), or Real-world operational validity: not established. Every report opens with: "These results measure recovery of simulator-defined faults and do not demonstrate production outage-prediction accuracy."
- **Oracle-mask claims [D6, G4].** False-alarm figures are not deployed false-alarm rates. Reports must not use the phrases "deployed false-alarm rate", "production false-alarm rate" or "alert suppression during incidents works". The oracle mask must not be described as a feature of the system under test, and reports must not imply that a production system knows when a fault's return ramp ends.
- **Dev data.** dataset_kind = dev datasets cannot be used for results.
<!-- END VERBATIM SOURCE LINES 985-1162 -->
