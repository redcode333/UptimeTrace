"""Fault footprints, classes F1-F10 and the compatibility matrix (spec section 4.5)."""
from __future__ import annotations

from itertools import combinations

SIGNALS = ("cpu", "mem", "disk_root", "disk_data", "rx", "tx")
ALL = frozenset(SIGNALS)

CLASS_SIGNALS = {
    "F1": frozenset({"mem"}),
    "F2": frozenset({"disk_root"}),
    "F3": frozenset({"disk_data"}),
    "F4": frozenset({"cpu"}),
    "F5": frozenset({"cpu", "tx"}),
    "F6": frozenset({"disk_root", "cpu", "tx"}),
    "F7": frozenset({"disk_data", "cpu", "tx"}),
    "F8": frozenset({"cpu", "rx", "tx"}),
    "F9": frozenset({"cpu", "mem", "rx", "tx"}),
    "F10": ALL,
}
CLASSES = tuple(CLASS_SIGNALS)

# Frozen matrix, rows/columns F1..F10, copied from spec section 4.5 ("v" = compatible, "x" = conflict).
_ROWS = {
    "F1": "xvvvvvvvxx", "F2": "vxvvvxvvvx", "F3": "vvxvvvxvvx", "F4": "vvvxxxxxxx", "F5": "vvvxxxxxxx",
    "F6": "vxvxxxxxxx", "F7": "vvxxxxxxxx", "F8": "vvvxxxxxxx", "F9": "xvvxxxxxxx", "F10": "xxxxxxxxxx",
}
MATRIX = {a: {b: _ROWS[a][j] == "v" for j, b in enumerate(CLASSES)} for a in CLASSES}


def footprint_class(family: str, outcome: str, mount: str | None = None, panic: bool = False,
                    subtype: str | None = None) -> str:
    """outcome in {'incident', 'twin'}."""
    if family == "mem_leak":
        if outcome == "twin":
            return "F1"
        return "F10" if panic else "F9"
    if family == "disk_fill":
        root = mount == "/"
        if outcome == "twin":
            return "F2" if root else "F3"
        return "F6" if root else "F7"
    if family == "cpu_runaway":
        return "F4" if outcome == "twin" else "F5"
    if family == "net_flood":
        return "F8"
    if family == "pool_exhaustion":
        return "F9"
    if family == "silent_crash":
        return {"process_crash": "F9", "dependency_outage": "F8", "kernel_panic": "F10"}[subtype]
    raise ValueError(family)


def footprint_signals(cls: str) -> frozenset:
    return CLASS_SIGNALS[cls]


def compatible(a: str, b: str) -> bool:
    return MATRIX[a][b]


def compatible_from_signals(a: str, b: str) -> bool:
    return not (CLASS_SIGNALS[a] & CLASS_SIGNALS[b])


COMPAT_PAIRS = tuple(sorted((a, b) for a, b in combinations(CLASSES, 2) if MATRIX[a][b]))
