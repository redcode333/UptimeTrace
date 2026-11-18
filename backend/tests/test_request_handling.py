"""Order of checks: size -> content type -> JSON parse -> schema_version -> schema."""
import json

import pytest

from tests.factories import JSON_HEADERS, make_envelope, post, raw

pytestmark = pytest.mark.anyio
LIMIT = 1024 * 1024


def code(response) -> str:
    return response.json()["error"]["code"]


async def test_body_over_limit_is_413_even_when_declared_length_is_absent(client, count_rows):
    async def chunks():  # no Content-Length: httpx streams it chunked
        for _ in range(LIMIT // 1024 + 1):
            yield b" " * 1024

    r = await client.post("/api/v1/telemetry", content=chunks(), headers=JSON_HEADERS)
    assert (r.status_code, code(r)) == (413, "payload_too_large")
    assert count_rows() == 0


async def test_declared_oversize_is_413(client):
    r = await post(client, b" " * (LIMIT + 1))
    assert (r.status_code, code(r)) == (413, "payload_too_large")


async def test_body_of_exactly_the_limit_passes_the_size_check(client):
    body = b"{}" + b" " * (LIMIT - 2)
    r = await post(client, body)
    assert r.status_code == 422  # reached schema validation; empty object lacks required fields


async def test_size_is_checked_before_content_type(client):
    r = await post(client, b" " * (LIMIT + 1), headers={"content-type": "text/plain"})
    assert r.status_code == 413


@pytest.mark.parametrize("header", [None, "text/plain", "application/xml", "application/jsonx",
                                    "application/json; charset=latin-1"])
async def test_wrong_or_missing_content_type_is_415(client, count_rows, header):
    headers = {} if header is None else {"content-type": header}
    r = await post(client, make_envelope(), headers=headers)
    assert (r.status_code, code(r)) == (415, "unsupported_media_type")
    assert count_rows() == 0


@pytest.mark.parametrize("header", ["application/json", "Application/JSON; charset=utf-8",
                                    'application/json; charset="UTF-8"'])
async def test_json_content_type_variants_accepted(client, header):
    r = await post(client, make_envelope(), headers={"content-type": header})
    assert r.status_code == 202


@pytest.mark.parametrize("body", [
    b"{not json", b"", b'{"a": NaN}', b'{"a": Infinity}', b'{"a": -Infinity}',
    b"[]", b'"text"', b"42", b"null", b"\xff\xfe\xfa",
    pytest.param(b"[" * 100000, id="deeply-nested"),
])
async def test_malformed_json_is_400(client, body):
    r = await post(client, body)
    assert (r.status_code, code(r)) == (400, "malformed_json")
    assert "details" not in r.json()["error"]


async def test_nan_literal_inside_an_otherwise_valid_envelope_is_malformed_not_a_schema_error(client):
    text = raw(make_envelope()).decode().replace("42.5", "NaN")
    r = await post(client, text.encode())
    assert (r.status_code, code(r)) == (400, "malformed_json")


async def test_unsupported_version_is_400_with_supported_list_and_checked_before_schema(client):
    # The rest of the document is garbage: the version must win, giving ONE clear error.
    r = await post(client, {"schema_version": "2.0", "junk": True})
    assert r.status_code == 400
    assert r.json() == {"error": {
        "code": "unsupported_schema_version",
        "message": "Schema version '2.0' is not supported.",
        "supported_versions": ["1.0"],
    }}


@pytest.mark.parametrize("version", [1.0, 1, None, ["1.0"], {"v": "1.0"}])
async def test_non_string_version_is_422_not_400(client, version):
    r = await post(client, make_envelope(schema_version=version))
    assert (r.status_code, code(r)) == (422, "validation_error")


async def test_missing_version_is_422(client):
    envelope = make_envelope()
    del envelope["schema_version"]
    r = await post(client, envelope)
    assert (r.status_code, code(r)) == (422, "validation_error")
    assert any("schema_version" in d["message"] for d in r.json()["error"]["details"])


async def test_schema_failure_reports_contract_shaped_paths(client, count_rows):
    envelope = make_envelope()
    envelope["metrics"][0]["value"] = 100.5
    envelope["metrics"][1]["labels"] = {}
    envelope["unexpected"] = 1
    r = await post(client, envelope)
    assert r.status_code == 422
    body = r.json()["error"]
    assert body["code"] == "validation_error" and body["message"] == "Telemetry envelope failed validation."
    paths = {d["path"] for d in body["details"]}
    assert {"metrics[0].value", "metrics[1].labels", "$"} <= paths
    assert all(set(d) == {"path", "message"} for d in body["details"])
    assert count_rows() == 0


async def test_non_database_exception_is_500_internal_error(client, monkeypatch):
    import app.main as main

    def boom(*_a, **_k):
        raise RuntimeError("secret internal detail")

    monkeypatch.setattr(main, "store_envelope", boom)
    r = await post(client, make_envelope())
    assert (r.status_code, code(r)) == (500, "internal_error")
    assert "secret internal detail" not in r.text
    assert json.loads(r.text)["error"].keys() == {"code", "message"}
