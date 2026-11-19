"""Build the normalised layout for an adopted external dataset.

    python -m ml.external.build --dataset smd [--data-root <path>]
The data root defaults to the UPTIMETRACE_DATA_ROOT environment variable (e.g. D:/UptimeTrace-ML/external-data).
Reads <data-root>/<dataset>/raw/PROVENANCE.json (written when the raw files were retrieved) and the raw files.
Writes <data-root>/<dataset>/normalised/v<adapter_version>/ (never inside raw/). Refuses to run if the provenance commit does not
match the downloaded tree, so numbers can always be traced to one exact upstream revision.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from ml.external import ADAPTER_VERSION, psm, smd
from ml.external.common import SourceInfo, sha256_file

ADAPTERS = {"smd": smd.normalise, "psm": psm.normalise}


def source_from(prov: dict) -> SourceInfo:
    return SourceInfo(dataset=prov["_dataset"], repository=prov["repository"], commit=prov["commit"], archive_sha256=prov["archive_sha256"],
                      retrieved_utc=prov["retrieved_utc"], licence_name=prov["licence_name"], licence_file_sha256=prov["licence_file_sha256"],
                      attribution=prov["attribution"], paper=prov["paper"])


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dataset", required=True, choices=sorted(ADAPTERS))
    ap.add_argument("--data-root", type=Path, default=os.environ.get("UPTIMETRACE_DATA_ROOT"),
                    help="defaults to $UPTIMETRACE_DATA_ROOT")
    a = ap.parse_args(argv)
    if a.data_root is None:
        ap.error("--data-root not given and UPTIMETRACE_DATA_ROOT is not set")
    a.data_root = Path(a.data_root)
    ds_root = a.data_root / a.dataset
    prov = json.loads((ds_root / "raw" / "PROVENANCE.json").read_text(encoding="utf-8"))
    prov["_dataset"] = a.dataset
    raw_root = a.data_root / prov["raw_root"]          # stored relative to the data root so the tree can be moved
    if prov["commit"] not in str(raw_root):
        raise SystemExit("provenance commit does not match the raw tree path; refusing to build")
    lic = ds_root / "raw" / "src" / prov["licence_file"]
    if sha256_file(lic) != prov["licence_file_sha256"]:
        raise SystemExit("licence file changed since retrieval; refusing to build")
    out = ds_root / "normalised" / f"v{ADAPTER_VERSION}"
    man = ADAPTERS[a.dataset](raw_root, out, source_from(prov))
    print(json.dumps({"dataset": a.dataset, "out": str(out), "files": man["totals"]["files"], "rows": man["totals"]["rows"],
                      "nan_cells": man["totals"]["nan_cells"]}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
