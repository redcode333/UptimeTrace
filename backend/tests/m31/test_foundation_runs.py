"""Run foundation: runs are never overwritten by accident, every folder is indexed by SHA-256, the same seed reproduces the
same files and a different seed does not, and every recorded fault carries its simulated outcome on the run's clock.

These use one prepared run so they stay quick. The full-dataset checks live in the audit scripts and test_determinism.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime
from pathlib import Path

import pytest

from simulator_v2.generate import DatasetExists, generate_dataset

ONE_RUN = lambda r: r.run_index == 95          # a validation run; one run keeps these tests quick


def _gen(root: Path, seed: int = 0, **kw):
    return generate_dataset(seed, root, workers=1, run_filter=ONE_RUN, label="run", validate=False, **kw)


def _sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


@pytest.fixture(scope="module")
def dataset(tmp_path_factory):
    return _gen(tmp_path_factory.mktemp("first") / "out")


def _ts(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


class TestOverwriteGuard:
    def test_a_generated_dataset_is_not_replaced_without_asking(self, tmp_path):
        root = _gen(tmp_path)
        before = _sha(root / "manifest.json")
        with pytest.raises(DatasetExists, match="already holds a generated dataset"):
            _gen(tmp_path)
        assert _sha(root / "manifest.json") == before

    def test_replacement_happens_only_when_overwrite_is_passed(self, tmp_path):
        root = _gen(tmp_path)
        (root / "stale_note.txt").write_text("from an earlier attempt")
        again = _gen(tmp_path, overwrite=True)
        assert again == root
        assert not (root / "stale_note.txt").exists(), "a replaced dataset must not keep stale files"
        assert (root / "manifest.json").exists()

    def test_a_folder_that_is_not_a_dataset_is_never_cleared(self, tmp_path):
        root = tmp_path / "run" / "run"
        root.mkdir(parents=True)
        keep = root / "notes.txt"
        keep.write_text("someone else's work")
        with pytest.raises(DatasetExists, match="holds no dataset manifest"):
            _gen(tmp_path, overwrite=True)
        assert keep.read_text() == "someone else's work"


class TestFileIndex:
    def test_index_lists_every_file_with_its_size_and_hash(self, dataset):
        index = json.loads((dataset / "file_index.json").read_text())
        listed = {e["path"]: e for e in index["files"]}
        on_disk = sorted(p.relative_to(dataset).as_posix() for p in dataset.rglob("*")
                         if p.is_file() and p.name not in ("file_index.json", "manifest.json"))
        assert sorted(listed) == on_disk
        for path, entry in listed.items():
            assert entry["sha256"] == _sha(dataset / path), path
            assert entry["bytes"] == (dataset / path).stat().st_size, path
        assert index["dataset_id"] == dataset.name
        manifest = json.loads((dataset / "manifest.json").read_text())
        manifest.pop("resources")
        assert index["manifest"]["content_sha256_without_resources"] == hashlib.sha256(
            json.dumps(manifest, sort_keys=True).encode("utf-8")).hexdigest()

    def test_a_changed_copy_no_longer_matches_the_index(self, dataset, tmp_path):
        copy = tmp_path / "copy.jsonl"
        copy.write_bytes((dataset / "faults.jsonl").read_bytes())
        index = {e["path"]: e for e in json.loads((dataset / "file_index.json").read_text())["files"]}
        assert _sha(copy) == index["faults.jsonl"]["sha256"]
        copy.write_bytes(copy.read_bytes().replace(b'"severity"', b'"severitx"', 1))
        assert _sha(copy) != index["faults.jsonl"]["sha256"]


class TestReproducibility:
    def test_the_same_seed_reproduces_the_same_files(self, dataset, tmp_path):
        again = _gen(tmp_path, seed=0)
        for rel in ("faults.jsonl", "incidents.jsonl", "runs.jsonl", "gaps.jsonl", "benign_episodes.jsonl"):
            assert _sha(dataset / rel) == _sha(again / rel), rel

    def test_a_different_seed_produces_different_ground_truth(self, dataset, tmp_path):
        other = _gen(tmp_path, seed=1)
        assert _sha(dataset / "faults.jsonl") != _sha(other / "faults.jsonl")


class TestGroundTruthRecords:
    @staticmethod
    def _rows(root: Path, name: str):
        return [json.loads(line) for line in (root / name).read_text().splitlines() if line]

    def test_every_recorded_fault_carries_its_simulated_outcome_and_no_failed_injection_is_recorded(self, dataset):
        faults = self._rows(dataset, "faults.jsonl")
        assert faults, "the run should contain faults"
        assert all(f["outcome"] == f["planned_outcome"] for f in faults)

    def test_fault_timestamps_sit_inside_the_run_and_follow_the_fault_clock(self, dataset):
        runs = {r["run_id"]: r for r in self._rows(dataset, "runs.jsonl")}
        for f in self._rows(dataset, "faults.jsonl"):
            run = runs[f["run_id"]]
            start, end = _ts(run["start_ts"]), _ts(run["end_ts"])
            assert start < end
            for key in ("start", "conflict_start", "impact_start", "resolved_time"):
                if f.get(key):
                    assert start <= _ts(f[key]) <= end, (f["fault_id"], key)
            assert _ts(f["conflict_start"]) <= _ts(f["conflict_end"]), f["fault_id"]
            # A precursor is an observable deviation that starts after the injection and before the impact it leads to.
            if f.get("precursor_start"):
                assert _ts(f["start"]) <= _ts(f["precursor_start"]), f["fault_id"]
                if f.get("impact_start"):
                    assert _ts(f["precursor_start"]) <= _ts(f["impact_start"]), f["fault_id"]
                    lead = (_ts(f["impact_start"]) - _ts(f["precursor_start"])).total_seconds()
                    assert f["precursor_lead_s"] == lead, f["fault_id"]
            if f.get("impact_start") and f.get("impact_end"):
                assert _ts(f["impact_start"]) <= _ts(f["impact_end"]), f["fault_id"]

    def test_fault_and_incident_records_use_the_same_run_identifier(self, dataset):
        run_ids = {r["run_id"] for r in self._rows(dataset, "runs.jsonl")}
        for name in ("faults.jsonl", "incidents.jsonl", "benign_episodes.jsonl", "gaps.jsonl"):
            assert {r["run_id"] for r in self._rows(dataset, name)} <= run_ids, name
