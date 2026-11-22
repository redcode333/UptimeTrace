"""Findings and the verdict. A HARD finding fails validation; a WARN finding is reported and does not. Every finding names the exact
count and the affected run ids, so nothing is summarised away."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field

MAX_RUN_IDS = 50


@dataclass(frozen=True)
class Finding:
    severity: str            # "hard" | "warn"
    area: str                # raw | labels | features | leakage | splits | coverage | manifest
    code: str                # stable identifier, e.g. "labels.target_mismatch"
    message: str
    count: int = 0
    run_ids: tuple[str, ...] = ()

    def to_dict(self) -> dict:
        d = asdict(self)
        d["run_ids"] = list(self.run_ids[:MAX_RUN_IDS])
        d["run_ids_total"] = len(self.run_ids)
        return d


@dataclass
class Report:
    dataset_id: str
    validator_version: str
    findings: list[Finding] = field(default_factory=list)
    stats: dict = field(default_factory=dict)
    checked: list[str] = field(default_factory=list)

    def add(self, severity: str, area: str, code: str, message: str, count: int = 0, run_ids=()) -> None:
        if severity not in ("hard", "warn"):
            raise ValueError(severity)
        self.findings.append(Finding(severity, area, code, message, int(count), tuple(sorted(set(run_ids)))))

    def check(self, name: str) -> None:
        self.checked.append(name)

    @property
    def hard(self) -> list[Finding]:
        return [f for f in self.findings if f.severity == "hard"]

    @property
    def warnings(self) -> list[Finding]:
        return [f for f in self.findings if f.severity == "warn"]

    @property
    def verdict(self) -> str:
        return "FAIL" if self.hard else "PASS"

    def to_dict(self) -> dict:
        return {
            "dataset_id": self.dataset_id,
            "validator_version": self.validator_version,
            "verdict": self.verdict,
            "hard_failures": len(self.hard),
            "warnings": len(self.warnings),
            "checks_run": self.checked,
            "findings": [f.to_dict() for f in self.findings],
            "stats": self.stats,
        }
