"""Executable checks for the v1 telemetry contract.

Run:  python -m unittest discover -s contracts/telemetry/tests -v
Needs: jsonschema >= 4 (nothing else). The schema is deliberately self-contained: it
does NOT rely on `format` assertion, so a plain validator enforces every rule below.
"""
import calendar
import datetime
import json
import unittest
import uuid
from pathlib import Path

from jsonschema import Draft202012Validator

V1 = Path(__file__).resolve().parent.parent / "v1"
EXAMPLES = V1 / "examples"
SCHEMA = json.loads((V1 / "envelope.schema.json").read_text(encoding="utf-8"))
EXPECTED = json.loads((EXAMPLES / "expected-failures.json").read_text(encoding="utf-8"))["fixtures"]


def _reject_constant(name):
    raise ValueError(f"non-JSON constant {name}")


def load(path):
    # Mirrors the parser requirement in the README: NaN/Infinity literals are malformed JSON.
    return json.loads(path.read_text(encoding="utf-8"), parse_constant=_reject_constant)


def pointer(error):
    return "".join("/" + str(p) for p in error.absolute_path)


class ContractTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        Draft202012Validator.check_schema(SCHEMA)
        cls.validator = Draft202012Validator(SCHEMA)  # no format_checker, on purpose
        ts_schema = {"$ref": "#/$defs/timestamp", "$defs": SCHEMA["$defs"]}
        cls.ts_validator = Draft202012Validator(ts_schema)

    def test_valid_examples_pass(self):
        paths = sorted((EXAMPLES / "valid").glob("*.json"))
        self.assertGreaterEqual(len(paths), 3)
        for path in paths:
            with self.subTest(example=path.name):
                errors = [(pointer(e), e.validator) for e in self.validator.iter_errors(load(path))]
                self.assertEqual([], errors)
        # envelope_id: present, a real canonical UUID, and distinct per example envelope.
        ids = [load(p)["envelope_id"] for p in paths]
        for i in ids:
            self.assertEqual(i, str(uuid.UUID(i)))
        self.assertEqual(len(ids), len(set(ids)))

    def test_every_invalid_example_fails_for_its_intended_rule(self):
        paths = sorted((EXAMPLES / "invalid").glob("*.json"))
        # No fixture without a declared expectation, and no stale expectation.
        self.assertEqual(sorted(p.name for p in paths), sorted(EXPECTED))
        for path in paths:
            with self.subTest(example=path.name):
                actual = {(pointer(e), e.validator) for e in self.validator.iter_errors(load(path))}
                expected = {(x["path"], x["keyword"]) for x in EXPECTED[path.name]}
                self.assertEqual(expected, actual)

    def test_envelope_id_accepts_only_canonical_uuids(self):
        uuid_validator = Draft202012Validator({"$ref": "#/$defs/uuid", "$defs": SCHEMA["$defs"]})
        ok = [str(uuid.uuid4()), "0190f3a2-6b1e-7c4d-9a3f-5d2e8b7a1c04"]
        bad = ["00000000-0000-0000-0000-000000000000",           # nil UUID
               "7C9E6679-7425-40DE-944B-E07FC1F90AE7",           # uppercase
               "7c9e6679742540de944be07fc1f90ae7",               # no hyphens
               "{7c9e6679-7425-40de-944b-e07fc1f90ae7}",         # braces
               "7c9e6679-7425-90de-944b-e07fc1f90ae7",           # version 9
               "7c9e6679-7425-40de-c44b-e07fc1f90ae7",           # wrong variant
               str(uuid.uuid4()) + "\n"]  # trailing newline
        for s in ok:
            self.assertTrue(uuid_validator.is_valid(s), s)
        for s in bad:
            self.assertFalse(uuid_validator.is_valid(s), repr(s))

    def test_timestamp_calendar_matches_real_calendar(self):
        """Every (year, month, day) the pattern accepts is a real date, and vice versa."""
        years = [1000, 1900, 1999, 2000, 2023, 2024, 2025, 2026, 2100, 2400, 9996, 9999]
        for y in years:
            for m in range(0, 14):
                for d in range(0, 33):
                    real = 1 <= m <= 12 and 1 <= d <= calendar.monthrange(y, m)[1]
                    ts = f"{y:04d}-{m:02d}-{d:02d}T12:00:00Z"
                    self.assertEqual(real, self.ts_validator.is_valid(ts), ts)

    def test_timestamp_utc_only_and_boundaries(self):
        ok = ["2026-11-10T00:00:00Z", "2026-11-10T23:59:59Z", "2026-11-10T09:30:00.1Z",
              "2026-11-10T09:30:00.123456Z", "1000-01-01T00:00:00Z", "9999-12-31T23:59:59.999999Z"]
        bad = ["2026-11-10T09:30:00z", "2026-11-10t09:30:00Z", "2026-11-10 09:30:00Z",
               "2026-11-10T09:30:00.Z", "2026-11-10T09:30:00.1234567Z", "2026-11-10T09:30:00-00:00",
               "2026-11-10T09:30:00+00:00", "2026-11-10T04:30:00-05:00", "2026-11-10T09:30Z",
               "2026-11-10", "0999-12-31T00:00:00Z", "2026-11-10T09:60:00Z",
               "٢٠٢٦-11-10T09:30:00Z"]  # Arabic-Indic digits: ASCII digits only
        for ts in ok:
            self.assertTrue(self.ts_validator.is_valid(ts), ts)
        for ts in bad:
            self.assertFalse(self.ts_validator.is_valid(ts), ts)
        # Round-trip sanity: accepted strings denote the instant they claim, in UTC.
        parsed = datetime.datetime.fromisoformat("2026-11-10T09:30:00.123456Z")
        self.assertEqual(datetime.timedelta(0), parsed.utcoffset())


if __name__ == "__main__":
    unittest.main()
