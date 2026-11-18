"""Dataset-level allocation: runs, archetypes, per-run primary quotas, relapse designation (spec sections 5, 10)."""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from . import constants as C
from .rng import global_stream


@dataclass
class RunSpec:
    run_index: int
    run_id: str
    split: str
    archetype: str
    ood_slow: bool
    primaries: list = field(default_factory=list)      # [(family, gets_relapse)]


def _balanced(n: int, rng) -> list[int]:
    """Counts over the 4 known archetypes differing by at most one; remainder archetypes chosen by the seeded stream."""
    base, rem = divmod(n, len(C.ARCHETYPES))
    counts = [base] * len(C.ARCHETYPES)
    for j in rng.choice(len(C.ARCHETYPES), size=rem, replace=False):
        counts[int(j)] += 1
    return counts


def allocate(master_seed: int) -> tuple[list[RunSpec], dict]:
    rng = global_stream(master_seed, "alloc")
    runs: list[RunSpec] = []
    arche_doc: dict = {}
    idx = 0
    for split in C.SPLITS:
        n = C.SPLIT_RUNS[split]
        labels: list[tuple[str, bool]] = []
        if split == "test_ood":
            labels += [(C.OOD_NEW_ARCHETYPE, False)] * 10                    # explicit: 10 unseen cache_node runs
            counts = _balanced(10, rng)                                      # explicit: 10 known-archetype runs, slow ramps
            for a, c in zip(C.ARCHETYPES, counts):
                labels += [(a, True)] * c
            arche_doc[split] = {C.OOD_NEW_ARCHETYPE: 10, **dict(zip(C.ARCHETYPES, counts)), "known_slow_ramps": 2.5}
        else:
            counts = _balanced(n, rng)
            for a, c in zip(C.ARCHETYPES, counts):
                labels += [(a, False)] * c
            arche_doc[split] = dict(zip(C.ARCHETYPES, counts))
        order = rng.permutation(len(labels))
        for j in order:
            arch, slow = labels[int(j)]
            runs.append(RunSpec(idx, f"r{idx:04d}", split, arch, slow))
            idx += 1
    # primaries: equal-probability multinomial per (split, family), per-run cap with overflow reassignment
    for split in C.SPLITS:
        rs = [r for r in runs if r.split == split]
        R = len(rs)
        total = np.zeros(R, dtype=int)
        per_fam: dict[str, np.ndarray] = {}
        for fam in C.FAMILIES:
            c = rng.multinomial(C.QUOTAS[fam][split], [1.0 / R] * R)
            per_fam[fam] = c
            total += c
            over = total - C.PRIMARY_CAP_PER_RUN
            for j in np.nonzero(over > 0)[0]:
                for _ in range(int(over[j])):
                    room = np.nonzero(total < C.PRIMARY_CAP_PER_RUN)[0]
                    k = int(rng.choice(room))
                    per_fam[fam][j] -= 1; total[j] -= 1
                    per_fam[fam][k] += 1; total[k] += 1
        for j, r in enumerate(rs):
            r.primaries = [(fam, False) for fam in C.FAMILIES for _ in range(int(per_fam[fam][j]))]
        # relapse designation: distinct candidate primaries, one each
        cands = [(j, k) for j, r in enumerate(rs) for k, (fam, _) in enumerate(r.primaries) if fam in C.RELAPSE_FAMILIES]
        pick = rng.choice(len(cands), size=C.SECONDARIES[split], replace=False)
        for q in pick:
            j, k = cands[int(q)]
            fam, _ = rs[j].primaries[k]
            rs[j].primaries[k] = (fam, True)
    doc = {"archetype_counts": arche_doc,
           "rule": "known archetypes: equal counts differing by at most one; remainder archetypes chosen by the seeded "
                   "allocation stream; test_ood: 10 cache_node + 10 known archetypes with 2.5x slower ramps (explicit in spec)"}
    return runs, doc
