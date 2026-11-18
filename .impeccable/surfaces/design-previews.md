---
version: 1
slug: "design-previews"
primary_target: "design-previews"
related_targets: []
---

# Surface brief: UptimeTrace console (design previews)

Mode: Operate. Audience: small-team infra / on-call engineer, desktop first. Job: answer "what needs me now?", then drill into a host, metric, alert or risk. Proof: backend evidence only; preview data is labelled PREVIEW DATA.
Chosen direction: Synoptic Forecast (IMPECCABLE'S PICK). Memorable moment: the NOW line, where measured history ends and the model outlook begins.
Unresolved: chart library, font hosting, light theme.

## Direction contract

THESIS: Prediction is never confirmation. Every time series is split at NOW: solid measured history on the left, a dashed shaded outlook on the right. Refuses the all-green KPI-card dashboard where forecast and fact share one number.
OWN-WORLD: Chart-room navy ground (#0b1722), panels one step lighter with 1px blue-grey seams, observed ink near-white, one amber (#f2b64a) reserved for outlook/experimental, warning orange and teal for confirmed state. Public Sans for UI, Spline Sans Mono for figures. Flags are bordered uppercase words, not coloured dots.
STORY: The engineer reads a bulletin: warnings in force (confirmed), outlook (experimental, dashed), no report (silent agents). They believe the numbers because source (LIVE/SYNTHETIC/REPLAY) is printed on every row, and act by opening the host.
FIRST VIEWPORT: 232px sidebar; top bar with search, time range, source filter, alerts; main left an observed-plus-outlook CPU chart over a host table (sparkline, source, value, state flag); right a 420px Bulletin panel with Warnings in force, Outlook, No report, and the experimental notice.
FORM: Synoptic bulletin (candidate pick, not the assigned roll), seed: ea86118dffa4592d.

FINISH: unreviewed and undocumented is unfinished; this build ends with the finish review, the verdict, DESIGN.md, and every shipping raster carrying its provenance.
