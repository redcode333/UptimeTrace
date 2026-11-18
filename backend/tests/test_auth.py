"""P1: scoped agent credentials and host identity validation on POST /api/v1/telemetry.

No database needed: the app runs on an in-memory engine and the storage call is replaced by a spy, so these tests prove
exactly what must hold BEFORE anything is stored.
"""
import json

import httpx
import pytest
from sqlalchemy import create_engine

from app import auth, main
from app.auth import CredentialError, CredentialStore
from app.ingest.service import Outcome
from tests.conftest import FIXED_NOW
from tests.factories import make_envelope, raw

pytestmark = pytest.mark.anyio          # same marker the rest of the async suite uses (conftest provides the asyncio backend)

URL = "/api/v1/telemetry"
JSON = {"content-type": "application/json"}


@pytest.fixture
def creds(tmp_path):
    """Three real credentials issued through the same code path an operator uses, then loaded from the file."""
    f = tmp_path / "credentials.json"
    tokens = {
        "live": auth.issue(f, "edge-01", "agent-test", ["host-test"], "live"),
        "synthetic": auth.issue(f, "sim-1", "sim-agent-1", ["sim-host-1"], "synthetic"),
        "replay": auth.issue(f, "smd-1", "replay-agent-1", ["replay-m1", "replay-m2"], "replay"),
    }
    return {"file": f, "tokens": tokens, "store": CredentialStore.load(f)}


@pytest.fixture
def stored(monkeypatch):
    calls = []

    def spy(session_factory, envelope, digest):
        calls.append(envelope)
        return Outcome.CREATED
    monkeypatch.setattr(main, "store_envelope", spy)
    return calls


def client_for(store) -> httpx.AsyncClient:
    app = main.create_app(create_engine("sqlite://"), store, clock=lambda: FIXED_NOW)
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app, raise_app_exceptions=False), base_url="http://test")


def bearer(token) -> dict:
    return {**JSON, "authorization": f"Bearer {token}"}


async def post(client, body, headers):
    content = body if isinstance(body, bytes) else raw(body)
    return await client.post(URL, content=content, headers=headers)


# ------------------------------------------------------------------------------------------------ 401: authentication
async def test_no_token_is_401_and_nothing_is_read_or_stored(creds, stored):
    async with client_for(creds["store"]) as c:
        r = await post(c, b"this is not even json", JSON)          # would be 400 malformed_json if the body were parsed first
    assert r.status_code == 401 and r.json()["error"]["code"] == "unauthorized"
    assert r.headers["www-authenticate"] == "Bearer" and stored == []


@pytest.mark.parametrize("header", [
    "", "Bearer", "Bearer ", "Basic dXNlcjpwYXNz", "Bearer two tokens", "Bearer utk_" + "x" * 20, "utk_" + "y" * 40,
    "Bearer " + "A" * 300, "Bearer tokén_with_non_ascii_chars_000000",
])
async def test_malformed_or_unknown_tokens_get_the_same_401(creds, stored, header):
    async with client_for(creds["store"]) as c:
        r = await post(c, make_envelope(), {**JSON, "authorization": header.encode("latin-1")})     # raw bytes, as a hostile client could send
    assert r.status_code == 401 and r.json() == {"error": {"code": "unauthorized", "message": "A valid agent token is required."}}
    assert stored == []


async def test_scheme_is_case_insensitive_but_the_token_is_exact(creds, stored):
    token = creds["tokens"]["live"]
    async with client_for(creds["store"]) as c:
        ok = await post(c, make_envelope(), {**JSON, "authorization": f"bearer {token}"})
        bad = await post(c, make_envelope(), bearer(token[:-1] + ("A" if token[-1] != "A" else "B")))
    assert ok.status_code == 202 and bad.status_code == 401 and len(stored) == 1


async def test_the_401_never_echoes_the_token(creds, stored):
    secret = "utk_" + "Z" * 40
    async with client_for(creds["store"]) as c:
        r = await post(c, make_envelope(), bearer(secret))
    assert secret not in r.text and secret not in str(r.headers)


async def test_health_stays_open_and_reveals_no_credential_data(creds):
    async with client_for(creds["store"]) as c:
        r = await c.get("/health")
    assert r.status_code == 200 and "token" not in r.text.lower() and "credential" not in r.text.lower()


# ------------------------------------------------------------------------------------------------ 202 / 403: scope
async def test_valid_token_with_matching_identity_is_accepted_and_stored_once(creds, stored):
    env = make_envelope()
    async with client_for(creds["store"]) as c:
        r = await post(c, env, bearer(creds["tokens"]["live"]))
    assert r.status_code == 202 and r.json()["envelope_id"] == env["envelope_id"]
    assert len(stored) == 1 and stored[0]["host_id"] == "host-test"


@pytest.mark.parametrize("override", [{"host_id": "host-other"}, {"agent_id": "agent-other"}, {"host_id": "sim-host-1"},
                                      {"host_id": "replay-m1"}])
async def test_valid_token_outside_its_scope_is_403_and_nothing_is_stored(creds, stored, override):
    async with client_for(creds["store"]) as c:
        r = await post(c, make_envelope(**override), bearer(creds["tokens"]["live"]))
    assert r.status_code == 403 and r.json()["error"]["code"] == "forbidden"
    assert stored == [] and next(iter(override.values())) not in r.text         # the response does not echo the claimed identity


async def test_each_kind_can_only_speak_for_its_own_hosts(creds, stored):
    t = creds["tokens"]
    async with client_for(creds["store"]) as c:
        # a synthetic or replay credential cannot pose as a live host, and a live one cannot pose as either
        assert (await post(c, make_envelope(), bearer(t["synthetic"]))).status_code == 403
        assert (await post(c, make_envelope(), bearer(t["replay"]))).status_code == 403
        assert (await post(c, make_envelope(agent_id="sim-agent-1", host_id="sim-host-1"), bearer(t["live"]))).status_code == 403
        assert (await post(c, make_envelope(agent_id="replay-agent-1", host_id="replay-m1"), bearer(t["live"]))).status_code == 403
        # and each succeeds for its own identity, including the second host of the replay credential
        assert (await post(c, make_envelope(agent_id="sim-agent-1", host_id="sim-host-1"), bearer(t["synthetic"]))).status_code == 202
        assert (await post(c, make_envelope(agent_id="replay-agent-1", host_id="replay-m2"), bearer(t["replay"]))).status_code == 202
    assert [e["host_id"] for e in stored] == ["sim-host-1", "replay-m2"]


async def test_schema_errors_are_reported_before_scope_for_an_authenticated_agent(creds, stored):
    env = make_envelope(host_id="host-other")
    env["metrics"] = []                                                          # invalid AND out of scope
    async with client_for(creds["store"]) as c:
        r = await post(c, env, bearer(creds["tokens"]["live"]))
    assert r.status_code == 422 and stored == []


async def test_authenticated_malformed_requests_keep_their_existing_status_codes(creds, stored):
    async with client_for(creds["store"]) as c:
        h = bearer(creds["tokens"]["live"])
        assert (await post(c, b"{nope", h)).status_code == 400
        assert (await c.post(URL, content=raw(make_envelope()), headers={"authorization": h["authorization"], "content-type": "text/plain"})).status_code == 415
    assert stored == []


# ------------------------------------------------------------------------------------------------ fail closed + file hygiene
async def test_a_store_with_no_credentials_rejects_everything():
    async with client_for(CredentialStore([])) as c:
        r = await post(c, make_envelope(), bearer("utk_" + "a" * 40))
    assert r.status_code == 401


def test_the_api_refuses_to_start_without_credentials(tmp_path):
    with pytest.raises(CredentialError, match="not found"):
        CredentialStore.load(tmp_path / "missing.json")
    empty = tmp_path / "empty.json"
    empty.write_text(json.dumps({"version": 1, "credentials": []}))
    with pytest.raises(CredentialError, match="no credentials"):
        CredentialStore.load(empty)


def test_the_file_holds_only_hashes_and_the_token_is_shown_once(creds):
    text = creds["file"].read_text()
    for token in creds["tokens"].values():
        assert token not in text and auth.hash_token(token) in text
    assert len(set(creds["tokens"].values())) == 3
    data = json.loads(text)
    assert {c["kind"] for c in data["credentials"]} == {"live", "synthetic", "replay"}
    assert all(set(c) == {"name", "token_sha256", "agent_id", "host_ids", "kind"} for c in data["credentials"])


def test_issue_revoke_rotate(creds):
    f = creds["file"]
    with pytest.raises(CredentialError, match="already exists"):
        auth.issue(f, "edge-01", "agent-test", ["host-test"], "live")
    old = creds["tokens"]["live"]
    auth.revoke(f, "edge-01")
    assert CredentialStore.load(f).authenticate(f"Bearer {old}") is None
    new = auth.issue(f, "edge-01", "agent-test", ["host-test"], "live")
    store = CredentialStore.load(f)
    assert new != old and store.authenticate(f"Bearer {new}").name == "edge-01" and store.authenticate(f"Bearer {old}") is None
    with pytest.raises(CredentialError, match="no credential named"):
        auth.revoke(f, "nope")


@pytest.mark.parametrize("kind,agent,hosts,message", [
    ("live", "a-1", ["sim-host-1"], "reserved"), ("live", "a-1", ["replay-x"], "reserved"), ("live", "a-1", ["sim9-old"], "reserved"),
    ("synthetic", "sim-a", ["web-01"], "starting with 'sim-'"), ("replay", "r-a", ["web-01"], "starting with 'replay-'"),
    ("live", "a-1", [], "at least one host_id"), ("live", "a-1", ["*"], "invalid host_id"), ("live", "*", ["h-1"], "invalid agent_id"),
    ("admin", "a-1", ["h-1"], "kind must be"), ("live", "a-1", ["bad host"], "invalid host_id"),
])
def test_scope_rules_reject_impersonation_and_wildcards(tmp_path, kind, agent, hosts, message):
    with pytest.raises(CredentialError, match=message):
        auth.issue(tmp_path / "c.json", "x-1", agent, hosts, kind)
    assert not (tmp_path / "c.json").exists()                                   # nothing half-written


def test_a_hand_edited_file_cannot_smuggle_in_a_wildcard_or_a_bad_kind(creds):
    data = json.loads(creds["file"].read_text())
    data["credentials"][0]["host_ids"] = ["*"]
    creds["file"].write_text(json.dumps(data))
    with pytest.raises(CredentialError):
        CredentialStore.load(creds["file"])
    data["credentials"][0]["host_ids"] = ["host-test"]
    data["credentials"].append(dict(data["credentials"][0]))                    # duplicate name and hash
    creds["file"].write_text(json.dumps(data))
    with pytest.raises(CredentialError, match="duplicate"):
        CredentialStore.load(creds["file"])


def test_cli_issue_prints_the_token_once_and_list_never_shows_hashes(tmp_path, capsys):
    f = tmp_path / "c.json"
    assert auth.main(["--file", str(f), "issue", "--name", "e1", "--agent-id", "agent-e1", "--host-id", "e1", "--kind", "live"]) == 0
    out = capsys.readouterr().out
    token = out.strip().splitlines()[-1]
    assert token.startswith("utk_") and CredentialStore.load(f).authenticate(f"Bearer {token}").name == "e1"
    assert auth.main(["--file", str(f), "list"]) == 0
    listing = capsys.readouterr().out
    assert "e1" in listing and token not in listing and auth.hash_token(token) not in listing
    assert auth.main(["--file", str(f), "issue", "--name", "e2", "--agent-id", "a", "--host-id", "sim-x", "--kind", "live"]) == 2


# ------------------------------------------------------------------------------------------------ the existing simulator can authenticate
def test_simulator_sends_the_bearer_token_when_given_one():
    from simulator.runner import run
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.headers.get("authorization"))
        return httpx.Response(202)
    client = httpx.Client(transport=httpx.MockTransport(handler), base_url="http://x")
    kw = dict(url="http://x", scenario="normal_baseline", count=2, interval=0, seed=1, agent_id="sim-agent-1", host_id="sim-host-1",
              hostname="h", service=None, client=client, sleep=lambda _s: None)
    run(token="utk_abc_def_123456789012", **kw)
    run(**kw)
    assert seen == ["Bearer utk_abc_def_123456789012"] * 2 + [None] * 2
