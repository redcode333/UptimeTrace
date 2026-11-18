"""Incident merging and the oracle-mask end (spec sections 6 and 13). All times are integer seconds from run_start."""
from __future__ import annotations

from . import constants as C


def merge_incidents(faults: list[dict]) -> list[dict]:
    """faults: incident faults only, each with fault_id, family, member_relation, impact_start, impact_end,
    conflict_end, and optionally group/silent_reason/precursor_lead_s/severity (primary's are used).

    A fault joins iff impact_start < running group recovery + 900 s (strict). The merge uses impact_end only;
    conflict_end plays no part in merging, cooldown or dedup (only in oracle_mask_end).
    """
    fs = sorted(faults, key=lambda f: (f["impact_start"], f["fault_id"]))
    groups: list[list[dict]] = []
    rec = None
    for f in fs:
        if groups and f["impact_start"] < rec + C.MERGE_WINDOW:
            groups[-1].append(f)
            rec = max(rec, f["impact_end"])
        else:
            groups.append([f])
            rec = f["impact_end"]
    out = []
    for g in groups:
        primary = next((m for m in g if m.get("member_relation") == "primary"), g[0])
        recovery = max(m["impact_end"] for m in g)
        cooldown_end = recovery + C.COOLDOWN
        max_conf = max(m["conflict_end"] for m in g)
        mask_end = max(cooldown_end, max_conf)
        out.append({
            "onset": min(m["impact_start"] for m in g),
            "recovery": recovery,
            "cooldown_end": cooldown_end,
            "oracle_mask_end": mask_end,
            "oracle_mask_end_source": "cooldown" if cooldown_end >= max_conf else "return_ramp",
            "primary_fault_id": primary["fault_id"],
            "family": primary["family"],
            "families": sorted({m["family"] for m in g}),
            "members": [{"fault_id": m["fault_id"], "family": m["family"], "member_relation": m.get("member_relation"),
                         "impact_start": m["impact_start"], "impact_end": m["impact_end"],
                         "conflict_end": m["conflict_end"]} for m in g],
            "severity": primary.get("severity"),
            "group": primary.get("group"),
            "silent_reason": primary.get("silent_reason"),
            "precursor_lead_s": primary.get("precursor_lead_s"),
            "n_members": len(g),
        })
    return out


def is_excluded_active(t: int, incidents: list[dict]) -> bool:
    """Oracle active-incident mask: onset <= t < oracle_mask_end for any incident (union)."""
    return any(i["onset"] <= t < i["oracle_mask_end"] for i in incidents)
