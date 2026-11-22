"""Command line:

  python -m dataset_validation validate --artifacts <artifacts> --dataset-id <id> --out <report.json> [--shallow]
  python -m dataset_validation freeze   --artifacts <artifacts> --dataset-id <id> --report <report.json> --out <manifest.json>
  python -m dataset_validation verify   --artifacts <artifacts> --dataset-id <id> --manifest <manifest.json>

`validate` never writes into the dataset. `freeze` refuses unless the report passed. `verify` exits non-zero on any change.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import manifest as M
from .report import Report
from .validate import validate_dataset, write_report


def _summary(r: Report) -> str:
    lines = [f"dataset {r.dataset_id}: verdict {r.verdict} ({len(r.hard)} hard failure(s), {len(r.warnings)} warning(s))"]
    for f in r.hard:
        lines.append(f"  HARD  [{f.code}] {f.message}" + (f" — {f.count} affected" if f.count else "") +
                     (f": {', '.join(f.run_ids[:10])}{' …' if len(f.run_ids) > 10 else ''}" if f.run_ids else ""))
    for f in r.warnings:
        lines.append(f"  warn  [{f.code}] {f.message}" + (f" — {f.count}" if f.count else ""))
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="dataset_validation")
    sub = p.add_subparsers(dest="cmd", required=True)
    v = sub.add_parser("validate")
    v.add_argument("--artifacts", type=Path, required=True)
    v.add_argument("--dataset-id", required=True)
    v.add_argument("--out", type=Path, required=True)
    v.add_argument("--shallow", action="store_true", help="skip the per-row observation reads (file presence and identity still checked)")
    v.add_argument("--derived-root", type=Path, default=None, help="versioned directory holding labels_full/ and features_full/")
    f = sub.add_parser("freeze")
    f.add_argument("--artifacts", type=Path, required=True)
    f.add_argument("--dataset-id", required=True)
    f.add_argument("--report", type=Path, required=True)
    f.add_argument("--out", type=Path, required=True)
    g = sub.add_parser("verify")
    g.add_argument("--artifacts", type=Path, required=True)
    g.add_argument("--dataset-id", required=True)
    g.add_argument("--manifest", type=Path, required=True)
    a = p.parse_args(argv)

    if a.cmd == "validate":
        report = validate_dataset(a.artifacts, a.dataset_id, deep=not a.shallow, derived_root=a.derived_root)
        write_report(report, a.out)
        print(_summary(report))
        print(f"report written to {a.out}")
        return 0 if report.verdict == "PASS" else 1
    if a.cmd == "freeze":
        report_data = json.loads(a.report.read_text(encoding="utf-8"))
        report = Report(dataset_id=report_data["dataset_id"], validator_version=report_data["validator_version"])
        report.stats = report_data["stats"]
        report.checked = report_data["checks_run"]
        for fd in report_data["findings"]:
            report.add(fd["severity"], fd["area"], fd["code"], fd["message"], fd["count"], fd["run_ids"])
        if report.verdict != "PASS":
            print(_summary(report))
            print("refused: the dataset did not pass validation, so no manifest was written")
            return 1
        man = M.freeze(a.artifacts, a.dataset_id, report, a.out)
        print(f"manifest written to {a.out}: {len(man['files'])} files, {man['total_bytes']} bytes")
        return 0
    result = M.verify(a.artifacts, a.dataset_id, a.manifest)
    print(json.dumps(result, indent=2))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
