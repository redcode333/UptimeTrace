"""Data-source classification: LIVE, SYNTHETIC or REPLAY.

The source is not stored per row. It is a property of the credential that sent the data, and `app.auth` ties the
credential kind to the host_id prefix (`sim<digits>-` synthetic, `replay-` replay, anything else live). We reuse those exact
rules, so the read API can never call a synthetic or replay host "live".
"""
from __future__ import annotations

from app.auth import REPLAY_HOST, SYNTHETIC_HOST

SOURCES = ("live", "synthetic", "replay")


def classify_source(host_id: str) -> str:
    if SYNTHETIC_HOST.match(host_id):
        return "synthetic"
    if REPLAY_HOST.match(host_id):
        return "replay"
    return "live"
