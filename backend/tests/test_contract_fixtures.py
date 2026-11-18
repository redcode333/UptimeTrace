"""The shared contract fixtures, run through the real endpoint (README: 'Python validation approach' #4)."""
import json
from pathlib import Path

import pytest

from app.config import REPO_ROOT
from app.ingest.schema import format_path
from tests.factories import post

pytestmark = pytest.mark.anyio

EXAMPLES = REPO_ROOT / "contracts" / "telemetry" / "v1" / "examples"
EXPECTED = json.loads((EXAMPLES / "expected-failures.json").read_text(encoding="utf-8"))["fixtures"]


def pointer_to_path(pointer: str) -> str:
    parts = [int(p) if p.isdigit() else p for p in pointer.split("/")[1:]]
    return format_path(parts)


async def test_every_valid_example_is_accepted_and_stored(client, count_rows):
    paths = sorted((EXAMPLES / "valid").glob("*.json"))
    assert paths
    for path in paths:
        body = path.read_bytes()
        r = await post(client, body)
        document = json.loads(body)
        assert r.status_code == 202, (path.name, r.text)
        assert r.json() == {"status": "accepted", "envelope_id": document["envelope_id"],
                            "accepted_metrics": len(document["metrics"])}
    assert count_rows() == len(paths)


async def test_every_invalid_example_is_rejected_at_exactly_the_declared_paths(client, count_rows):
    paths = sorted((EXAMPLES / "invalid").glob("*.json"))
    assert sorted(p.name for p in paths) == sorted(EXPECTED)
    for path in paths:
        r = await post(client, path.read_bytes())
        if path.name == "unsupported-schema-version.json":
            assert (r.status_code, r.json()["error"]["code"]) == (400, "unsupported_schema_version"), path.name
            continue
        assert r.status_code == 422, (path.name, r.status_code, r.text)
        actual = {d["path"] for d in r.json()["error"]["details"]}
        expected = {pointer_to_path(x["path"]) for x in EXPECTED[path.name]}
        assert actual == expected, path.name
    assert count_rows() == 0
