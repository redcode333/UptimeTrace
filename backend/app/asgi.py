"""Production entrypoint:  uvicorn app.asgi:app

Agent credentials come from the host registry (database; issue and revoke through /api/v1/registry, revocation is immediate).
A legacy credentials.json, if present, is still honoured. With neither, every envelope is rejected with 401: fail closed.
"""
from app.auth import CredentialStore
from app.clerk_auth import ClerkConfig, ClerkVerifier
from app.config import get_settings
from app.db import make_engine, make_session_factory
from app.registry.store import RegistryCredentialStore
from app.main import create_app
from app.traces.jaeger import JaegerClient
from app.traces.ingest import CredentialStore as TraceCredentialStore, TraceIngest, make_collector_forwarder

_settings = get_settings()
_engine = make_engine(_settings.database_url.get_secret_value(), _settings.db_connect_timeout_seconds)
_legacy = CredentialStore.load(_settings.credentials_file, require_any=False) if _settings.credentials_file.exists() else None
_mcp = None
if _settings.mcp_enabled:
    from app.mcp.server import build_mcp_server
    if not _settings.mcp_public_url or not _settings.mcp_allowed_hosts:
        raise RuntimeError("MCP_ENABLED requires MCP_PUBLIC_URL and MCP_ALLOWED_HOSTS (fails closed)")
    _mcp = build_mcp_server(make_session_factory(_engine), public_url=_settings.mcp_public_url, console_base_url=_settings.console_base_url,
                            allowed_hosts=[h.strip() for h in _settings.mcp_allowed_hosts.split(",") if h.strip()])
_trace_ingest = None
if _settings.trace_ingest_credentials_file and _settings.trace_collector_url and _settings.trace_collector_token:
    _trace_ingest = TraceIngest(make_session_factory(_engine), TraceCredentialStore(_settings.trace_ingest_credentials_file),
                                make_collector_forwarder(_settings.trace_collector_url, _settings.trace_collector_token.get_secret_value()))
app = create_app(
    _engine,
    credentials=RegistryCredentialStore(make_session_factory(_engine), fallback=_legacy),
    mcp_server=_mcp,
    alert_sweep_seconds=_settings.alert_sweep_seconds or None,
    trace_client=JaegerClient(_settings.trace_backend_url, _settings.trace_query_timeout_seconds) if _settings.trace_backend_url else None,
    trace_ingest=_trace_ingest,
    # Fails closed: the read API needs CLERK_ISSUER, CLERK_AUTHORIZED_PARTIES and UPTIMETRACE_ALLOWED_USER_IDS (see .env.example).
    verifier=ClerkVerifier(ClerkConfig.from_values(
        _settings.clerk_issuer, _settings.clerk_authorized_parties, _settings.uptimetrace_allowed_user_ids)),
)
