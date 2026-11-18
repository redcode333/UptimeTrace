"""Authentication boundary for the browser API. Real RS256 tokens minted with a local key; PostgreSQL-backed app."""
from __future__ import annotations

import time

import jwt
import pytest
from jwt.exceptions import PyJWKClientConnectionError

from app.clerk_auth import ClerkConfig
from app.main import create_app
from tests.clerk_support import (
    ALLOWED_USER, ISSUER, ORIGIN, OTHER_USER, StaticKeys, bearer, mint, private_key, verifier,
)
from tests.conftest import FIXED_NOW, TEST_CREDENTIALS, TEST_TOKEN, make_client
from tests.factories import make_envelope, post

pytestmark = pytest.mark.anyio


@pytest.fixture
def api(app_engine):
    def build(**kw):
        return make_client(create_app(app_engine, TEST_CREDENTIALS, clock=lambda: FIXED_NOW, **kw))
    return build


def get(client, path, headers=None):
    # make_client sets the agent bearer by default; browser requests must not carry it
    client.headers.pop("Authorization", None)
    return client.get(path, headers=headers or {})


async def test_valid_session_of_an_allowed_user_can_read(api):
    c = api(verifier=verifier())
    r = await get(c, "/api/v1/hosts", bearer())
    assert r.status_code == 200 and r.json()["total"] == 0


@pytest.mark.parametrize("name,headers", [
    ("no header", {}),
    ("wrong scheme", {"Authorization": "Basic abc"}),
    ("garbage token", {"Authorization": "Bearer not.a.jwt"}),
    ("empty bearer", {"Authorization": "Bearer "}),
    ("expired", bearer(exp_in=-60)),
    ("not yet valid", bearer(nbf_in=600)),
    ("wrong issuer", bearer(iss="https://evil.clerk.accounts.dev")),
    ("issuer with a trailing slash", bearer(iss=ISSUER + "/")),
    ("azp from another origin", bearer(azp="https://evil.example")),
    ("azp missing", bearer(azp=None)),
    ("unknown signing key", bearer(kid="ins_key_unknown")),
    ("signed by another private key", bearer(key=private_key("ins_key_other"))),
])
async def test_invalid_sessions_are_401_with_one_fixed_message(api, name, headers):
    r = await get(api(verifier=verifier()), "/api/v1/hosts", headers)
    assert r.status_code == 401, name
    assert r.json()["error"] == {"code": "unauthorized", "message": "A valid session token is required."}
    assert r.headers["www-authenticate"] == "Bearer"


async def test_forged_tokens_cannot_use_none_or_hmac_with_the_public_key(api):
    import base64, hashlib, hmac, json
    from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
    now = int(time.time())
    claims = {"sub": ALLOWED_USER, "iss": ISSUER, "iat": now, "exp": now + 60, "azp": ORIGIN}
    c = api(verifier=verifier())
    forged_none = jwt.encode(claims, key=None, algorithm="none", headers={"kid": "ins_key_1"})
    assert (await get(c, "/api/v1/hosts", {"Authorization": f"Bearer {forged_none}"})).status_code == 401
    # HS256 signed with the PUBLIC key as the HMAC secret (algorithm confusion), hand-built because PyJWT refuses it
    pem = private_key().public_key().public_bytes(Encoding.PEM, PublicFormat.SubjectPublicKeyInfo)
    b64 = lambda b: base64.urlsafe_b64encode(b).rstrip(b"=")  # noqa: E731
    head = b64(json.dumps({"alg": "HS256", "typ": "JWT", "kid": "ins_key_1"}).encode())
    body = b64(json.dumps(claims).encode())
    token = (head + b"." + body + b"." + b64(hmac.new(pem, head + b"." + body, hashlib.sha256).digest())).decode()
    assert (await get(c, "/api/v1/hosts", {"Authorization": f"Bearer {token}"})).status_code == 401


async def test_a_valid_session_that_is_not_allowlisted_is_403_but_can_learn_its_own_id(api):
    c = api(verifier=verifier())
    for path in ("/api/v1/hosts", "/api/v1/hosts/x", "/api/v1/hosts/x/metrics/latest"):
        r = await get(c, path, bearer(sub=OTHER_USER))
        assert r.status_code == 403 and r.json()["error"]["code"] == "forbidden", path
    me = await get(c, "/api/v1/me", bearer(sub=OTHER_USER))
    assert me.status_code == 200 and me.json() == {"user_id": OTHER_USER, "authorized": False}
    me = await get(c, "/api/v1/me", bearer())
    assert me.json() == {"user_id": ALLOWED_USER, "authorized": True}
    assert (await get(c, "/api/v1/me")).status_code == 401


async def test_key_service_outage_is_503_not_a_pass_and_not_a_401(api):
    down = StaticKeys(fail=PyJWKClientConnectionError("jwks down"))
    r = await get(api(verifier=verifier(down)), "/api/v1/hosts", bearer())
    assert r.status_code == 503 and r.json()["error"]["code"] == "auth_unavailable"


async def test_without_a_verifier_every_protected_route_is_closed_and_health_stays_public(api):
    c = api()  # no verifier configured
    assert (await get(c, "/api/v1/hosts", bearer())).status_code == 401
    assert (await get(c, "/api/v1/me", bearer())).status_code == 401
    assert (await get(c, "/health")).status_code == 200


async def test_health_is_public_and_agent_ingestion_is_unchanged_by_the_browser_auth(api):
    c = api(verifier=verifier())
    assert (await get(c, "/health")).status_code == 200
    # a Clerk session is not an agent credential: it cannot ingest
    r = await post(c, make_envelope(), headers={"content-type": "application/json", **bearer()})
    assert r.status_code == 401
    # and an agent credential is not a browser session: it cannot read
    r = await get(c, "/api/v1/hosts", {"Authorization": f"Bearer {TEST_TOKEN}"})
    assert r.status_code == 401
    ok = await post(c, make_envelope(), headers={"content-type": "application/json", "Authorization": f"Bearer {TEST_TOKEN}"})
    assert ok.status_code == 202


async def test_cors_allows_only_the_configured_origin_and_the_registry_methods(api):
    c = api(verifier=verifier())
    good = await c.options("/api/v1/hosts", headers={"Origin": ORIGIN, "Access-Control-Request-Method": "GET",
                                                     "Access-Control-Request-Headers": "authorization"})
    assert good.status_code == 200 and good.headers["access-control-allow-origin"] == ORIGIN
    bad = await c.options("/api/v1/hosts", headers={"Origin": "https://evil.example", "Access-Control-Request-Method": "GET"})
    assert "access-control-allow-origin" not in bad.headers
    for method in ("POST", "DELETE"):  # registry mutations (bearer header, no cookies, so no CSRF surface)
        ok = await c.options("/api/v1/registry/hosts", headers={"Origin": ORIGIN, "Access-Control-Request-Method": method})
        assert ok.status_code == 200
    for method in ("PUT", "PATCH"):
        no = await c.options("/api/v1/hosts", headers={"Origin": ORIGIN, "Access-Control-Request-Method": method})
        assert no.status_code == 400
    evil = await c.options("/api/v1/registry/hosts", headers={"Origin": "https://evil.example", "Access-Control-Request-Method": "POST"})
    assert "access-control-allow-origin" not in evil.headers


def test_configuration_fails_closed_and_names_only_the_missing_variables():
    with pytest.raises(ValueError) as err:
        ClerkConfig.from_values(None, None, None)
    text = str(err.value)
    assert "CLERK_ISSUER" in text and "CLERK_AUTHORIZED_PARTIES" in text and "UPTIMETRACE_ALLOWED_USER_IDS" in text
    with pytest.raises(ValueError):
        ClerkConfig.from_values(ISSUER, "*", ALLOWED_USER)          # wildcard origin refused
    with pytest.raises(ValueError):
        ClerkConfig.from_values("http://insecure.example", ORIGIN, ALLOWED_USER)
    cfg = ClerkConfig.from_values(ISSUER + "/", f"{ORIGIN}/ , https://app.example", f"{ALLOWED_USER}, user_b")
    assert cfg.issuer == ISSUER and cfg.authorized_parties == (ORIGIN, "https://app.example")
    assert cfg.jwks_url == ISSUER + "/.well-known/jwks.json" and cfg.allowed_user_ids == {ALLOWED_USER, "user_b"}
