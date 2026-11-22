"""Reading parquet files without letting one damaged file hide the rest of the dataset. A file that cannot be decoded is a hard finding
that names the file; the caller skips it and the other checks still run."""
from __future__ import annotations

from pathlib import Path

from .report import Report


def try_read(path: Path, columns: list[str] | None, r: Report, area: str, run_id: str | None = None):
    import pyarrow.parquet as pq

    try:
        return pq.read_table(path, columns=columns).to_pandas()
    except Exception as exc:  # noqa: BLE001 - any decode failure is reported with its file, never swallowed
        r.add("hard", area, f"{area}.unreadable",
              f"{path.name} cannot be decoded ({type(exc).__name__}: {str(exc)[:80]}); its checks were skipped",
              1, [run_id] if run_id else ())
        return None
