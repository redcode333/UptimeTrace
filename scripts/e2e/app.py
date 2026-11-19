"""The real API for the end-to-end workflow: the production app factory, a real PostgreSQL database, the real migrations and the
real least-privilege application role. Two things differ from production, both deliberate and recorded in the evidence:

  * Clerk: no Clerk tenant is available to this test, so browser sessions are verified against the local test key set
    (backend/tests/clerk_support.py). Agent ingestion is unaffected: it uses registry credentials exactly as in production.
  * The clock is the real clock, and the alert sweep runs every 2 seconds (production default: 30 seconds).

Started by scripts/e2e/run_workflow.py with E2E_APP_DATABASE_URL set (the uptimetrace_app role). Not for any shared deployment.
"""
import os
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[2] / "backend"
sys.path.insert(0, str(BACKEND))

from app.clerk_auth import ClerkConfig, ClerkVerifier  # noqa: E402
from app.db import make_engine, make_session_factory  # noqa: E402
from app.main import create_app  # noqa: E402
from app.registry.store import RegistryCredentialStore  # noqa: E402
from tests.clerk_support import ALLOWED_USER, ISSUER, ORIGIN, StaticKeys  # noqa: E402

engine = make_engine(os.environ["E2E_APP_DATABASE_URL"], 5)
verifier = ClerkVerifier(ClerkConfig.from_values(ISSUER, ORIGIN, ALLOWED_USER), StaticKeys())
app = create_app(engine, RegistryCredentialStore(make_session_factory(engine)), verifier=verifier, alert_sweep_seconds=2)
