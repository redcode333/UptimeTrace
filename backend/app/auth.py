"""Scoped agent credentials for the PoC (contract README: `Authorization: Bearer <agent token>`, bound to `agent_id`).

A credential is a random bearer token bound to ONE agent_id and an explicit list of host_ids, plus a kind. The server stores
only the SHA-256 of the token in a git-ignored JSON file; the token itself is shown once, when it is issued.

    python -m app.auth issue  --name edge-01 --agent-id agent-edge-01 --host-id edge-01 --kind live
    python -m app.auth issue  --name sim-1   --agent-id sim-agent-01  --host-id sim-host-01 --kind synthetic
    python -m app.auth list
    python -m app.auth revoke --name edge-01

Kind and host prefix are tied together so a credential cannot impersonate another source: `live` hosts may not start with
`sim-` / `replay-`, `synthetic` hosts must start with `sim-`, `replay` hosts must start with `replay-`.
The file is read once at startup (restart the API after issuing or revoking). This is a PoC mechanism, not a full auth system.
"""
from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import os
import re
import secrets
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

KINDS = ("live", "replay", "synthetic")
TOKEN_PREFIX = "utk_"
IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")       # the contract's identifier pattern (no wildcards)
NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
SYNTHETIC_HOST = re.compile(r"^sim\d*-")                              # same rule the existing simulator enforces
REPLAY_HOST = re.compile(r"^replay-")
TOKEN_CHARS = re.compile(r"^[A-Za-z0-9_-]{16,200}$")
FILE_VERSION = 1


class CredentialError(ValueError):
    pass


@dataclass(frozen=True)
class Credential:
    name: str
    token_sha256: str
    agent_id: str
    host_ids: tuple[str, ...] | None       # None = unrestricted; constructible only in code (tests), never from the file
    kind: str


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def check_scope(kind: str, agent_id: str, host_ids: list[str] | tuple[str, ...]) -> None:
    """Validate a credential's identity scope. Raises CredentialError naming the problem."""
    if kind not in KINDS:
        raise CredentialError(f"kind must be one of {KINDS}, got {kind!r}")
    if not IDENTIFIER.match(agent_id or ""):
        raise CredentialError(f"invalid agent_id {agent_id!r}")
    if not host_ids:
        raise CredentialError("at least one host_id is required (no wildcards)")
    for h in host_ids:
        if not IDENTIFIER.match(h or ""):
            raise CredentialError(f"invalid host_id {h!r}")
        synthetic, replay = bool(SYNTHETIC_HOST.match(h)), bool(REPLAY_HOST.match(h))
        if kind == "live" and (synthetic or replay):
            raise CredentialError(f"a live credential may not use the reserved host_id {h!r} ('sim-' / 'replay-')")
        if kind == "synthetic" and not synthetic:
            raise CredentialError(f"a synthetic credential needs host_ids starting with 'sim-', got {h!r}")
        if kind == "replay" and not replay:
            raise CredentialError(f"a replay credential needs host_ids starting with 'replay-', got {h!r}")


def _parse_bearer(header: str | None) -> str | None:
    if not header:
        return None
    parts = header.split(" ")
    if len(parts) != 2 or parts[0].lower() != "bearer" or not TOKEN_CHARS.match(parts[1]):
        return None
    return parts[1]


parse_bearer = _parse_bearer  # public alias for other credential stores (app/registry/store.py)


class CredentialStore:
    def __init__(self, credentials: list[Credential]):
        self._creds = list(credentials)

    def __len__(self) -> int:
        return len(self._creds)

    @classmethod
    def for_tests(cls, token: str, kind: str = "live") -> "CredentialStore":
        """Unrestricted credential for automated tests only. The file loader never produces one."""
        return cls([Credential("test", hash_token(token), "*", None, kind)])

    @classmethod
    def load(cls, path: Path, require_any: bool = True) -> "CredentialStore":
        path = Path(path)
        if not path.exists():
            raise CredentialError(f"credentials file not found: {path}. Issue one with: python -m app.auth issue ...")
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise CredentialError(f"credentials file unreadable: {type(exc).__name__}") from None
        if not isinstance(data, dict) or data.get("version") != FILE_VERSION or not isinstance(data.get("credentials"), list):
            raise CredentialError("credentials file has an unexpected format")
        creds, names, hashes = [], set(), set()
        for e in data["credentials"]:
            try:
                name, digest = e["name"], e["token_sha256"]
                agent_id, hosts, kind = e["agent_id"], e["host_ids"], e["kind"]
            except (KeyError, TypeError):
                raise CredentialError("credentials file entry is missing a field") from None
            if not NAME.match(str(name)) or not re.fullmatch(r"[0-9a-f]{64}", str(digest)):
                raise CredentialError(f"credential {name!r} is malformed")
            if name in names or digest in hashes:
                raise CredentialError(f"duplicate credential {name!r}")
            check_scope(kind, agent_id, hosts)
            names.add(name); hashes.add(digest)
            creds.append(Credential(name, digest, agent_id, tuple(hosts), kind))
        if require_any and not creds:
            raise CredentialError("credentials file contains no credentials: ingestion would reject every request")
        return cls(creds)

    def authenticate(self, authorization_header: str | None) -> Credential | None:
        """The credential for a valid bearer token, else None. Compares against every entry in constant time."""
        token = _parse_bearer(authorization_header)
        if token is None:
            return None
        digest = hash_token(token).encode()
        match = None
        for c in self._creds:
            if hmac.compare_digest(c.token_sha256.encode(), digest):
                match = c
        return match

    @staticmethod
    def permits(credential: Credential, envelope: dict) -> bool:
        """Host identity validation: the envelope's agent_id and host_id must be inside the credential's scope."""
        if credential.host_ids is None:
            return True
        return envelope.get("agent_id") == credential.agent_id and envelope.get("host_id") in credential.host_ids


# ----------------------------------------------------------------------------------------------- issuing (CLI)
def _read(path: Path) -> dict:
    if not path.exists():
        return {"version": FILE_VERSION, "credentials": []}
    CredentialStore.load(path, require_any=False)          # refuse to extend a file that is already invalid
    return json.loads(path.read_text(encoding="utf-8"))


def _write(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".cred-", suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    try:
        os.chmod(tmp, 0o600)                                # no-op on Windows; the file holds hashes only
    except OSError:
        pass
    os.replace(tmp, path)


def issue(path: Path, name: str, agent_id: str, host_ids: list[str], kind: str) -> str:
    """Create a credential and return its token (shown once; only the hash is stored)."""
    if not NAME.match(name):
        raise CredentialError(f"invalid credential name {name!r}")
    check_scope(kind, agent_id, host_ids)
    data = _read(Path(path))
    if any(c["name"] == name for c in data["credentials"]):
        raise CredentialError(f"credential {name!r} already exists (revoke it first to rotate)")
    token = TOKEN_PREFIX + secrets.token_urlsafe(32)
    data["credentials"].append({"name": name, "token_sha256": hash_token(token), "agent_id": agent_id,
                                "host_ids": list(host_ids), "kind": kind})
    _write(Path(path), data)
    return token


def revoke(path: Path, name: str) -> None:
    data = _read(Path(path))
    kept = [c for c in data["credentials"] if c["name"] != name]
    if len(kept) == len(data["credentials"]):
        raise CredentialError(f"no credential named {name!r}")
    data["credentials"] = kept
    _write(Path(path), data)


def main(argv: list[str] | None = None) -> int:
    from app.config import BACKEND_DIR
    default = os.environ.get("CREDENTIALS_FILE") or str(BACKEND_DIR / "credentials.json")
    p = argparse.ArgumentParser(prog="app.auth", description="Manage scoped agent credentials.")
    p.add_argument("--file", default=default, help="credentials file (default %(default)s)")
    sub = p.add_subparsers(dest="cmd", required=True)
    i = sub.add_parser("issue")
    i.add_argument("--name", required=True)
    i.add_argument("--agent-id", required=True)
    i.add_argument("--host-id", action="append", required=True, dest="host_ids")
    i.add_argument("--kind", choices=KINDS, required=True)
    r = sub.add_parser("revoke")
    r.add_argument("--name", required=True)
    sub.add_parser("list")
    a = p.parse_args(argv)
    path = Path(a.file)
    try:
        if a.cmd == "issue":
            token = issue(path, a.name, a.agent_id, a.host_ids, a.kind)
            print(f"credential '{a.name}' created ({a.kind}); agent_id={a.agent_id} host_ids={','.join(a.host_ids)}")
            print("TOKEN (shown once, store it securely):")
            print(token)
        elif a.cmd == "revoke":
            revoke(path, a.name)
            print(f"credential '{a.name}' revoked (restart the API to apply)")
        else:
            for c in CredentialStore.load(path, require_any=False)._creds:
                print(f"{c.name:20s} {c.kind:10s} agent={c.agent_id} hosts={','.join(c.host_ids or [])}")
    except CredentialError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
