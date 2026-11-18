"""Independent, reproducible random streams (spec section 17)."""
from __future__ import annotations

import numpy as np

COMPONENTS = ("profile", "workload", "noise", "benign", "faults", "planner", "gaps", "omissions",
              "jitter", "ids", "alloc")


def _gen(master_seed: int, spawn_key: tuple) -> np.random.Generator:
    return np.random.Generator(np.random.PCG64(np.random.SeedSequence(entropy=master_seed, spawn_key=spawn_key)))


class RunStreams:
    """Named Generators for one run: spawn_key = (run_index, component_id[, sub])."""

    def __init__(self, master_seed: int, run_index: int):
        self.master_seed = master_seed
        self.run_index = run_index
        self._cache: dict[tuple, np.random.Generator] = {}

    def spawn_key(self, component: str) -> tuple:
        return (self.run_index, COMPONENTS.index(component))

    def get(self, component: str, sub: int | None = None) -> np.random.Generator:
        key = (component, sub)
        if key not in self._cache:
            sk = self.spawn_key(component) + (() if sub is None else (sub,))
            self._cache[key] = _gen(self.master_seed, sk)
        return self._cache[key]

    def fresh(self, component: str, *sub: int) -> np.random.Generator:
        """A new generator on every call; the key is the full tuple (per-replan / per-slot / per-attempt streams)."""
        return _gen(self.master_seed, self.spawn_key(component) + tuple(int(x) for x in sub))


def global_stream(master_seed: int, name: str) -> np.random.Generator:
    """Dataset-level stream (quota allocation, archetype assignment)."""
    return _gen(master_seed, (10_000_000, sum(ord(c) * (i + 1) for i, c in enumerate(name))))
