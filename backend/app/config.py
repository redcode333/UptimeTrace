from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parent.parent
REPO_ROOT = BACKEND_DIR.parent
SCHEMA_PATH = REPO_ROOT / "contracts" / "telemetry" / "v1" / "envelope.schema.json"

SUPPORTED_SCHEMA_VERSIONS = ["1.0"]
MAX_BODY_BYTES = 1024 * 1024  # contract: max body 1 MiB


def to_sqlalchemy_url(url: str) -> str:
    """Select the psycopg 3 driver for a plain postgresql:// URL."""
    for prefix in ("postgresql://", "postgres://"):
        if url.startswith(prefix):
            return "postgresql+psycopg://" + url[len(prefix):]
    return url


class Settings(BaseSettings):
    """Configuration comes from the environment (backend/.env is a dev convenience).

    The database URL is a SecretStr so it never appears in repr()/logs.
    """

    model_config = SettingsConfigDict(env_file=BACKEND_DIR / ".env", extra="ignore")

    # Runtime identity: the least-privilege role `uptimetrace_app` (never the owner).
    database_url: SecretStr
    # Owner/migration identity, used only by Alembic. Falls back to database_url when unset.
    migration_database_url: SecretStr | None = None
    db_connect_timeout_seconds: int = 5
    # Hashed agent credentials (see app/auth.py). Ingestion refuses to start without at least one.
    credentials_file: Path = BACKEND_DIR / "credentials.json"

    # Clerk session verification for the browser API (see app/clerk_auth.py). All three are required to start the API.
    clerk_issuer: str | None = None
    clerk_authorized_parties: str | None = None
    uptimetrace_allowed_user_ids: str | None = None

    # Periodic alert evaluation (catches hosts that stop reporting). 0 disables it; ingestion still evaluates after each envelope.
    alert_sweep_seconds: float = 30.0

    # MCP interface: OFF by default. Enable only after reading docs/MCP_INTEGRATION.md (single-workspace deployments only today).
    mcp_enabled: bool = False
    mcp_public_url: str | None = None          # external base URL of this API, e.g. https://api.example.com
    mcp_allowed_hosts: str | None = None       # comma-separated Host header values (DNS-rebinding protection), e.g. api.example.com
    console_base_url: str | None = None        # web console base URL used to build deep links, e.g. https://app.example.com

    @property
    def sqlalchemy_url(self) -> str:
        return to_sqlalchemy_url(self.database_url.get_secret_value())

    @property
    def migration_sqlalchemy_url(self) -> str:
        url = self.migration_database_url or self.database_url
        return to_sqlalchemy_url(url.get_secret_value())


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]
