from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ErrorDetail:
    path: str
    message: str

    def as_dict(self) -> dict[str, str]:
        return {"path": self.path, "message": self.message}


class ApiError(Exception):
    """An error with a contract-defined status code and `error.code`."""

    def __init__(
        self,
        status: int,
        code: str,
        message: str,
        details: list[ErrorDetail] | None = None,
        extra: dict | None = None,
        headers: dict | None = None,
    ):
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message
        self.details = details
        self.extra = extra or {}
        self.headers = headers

    def body(self) -> dict:
        error: dict = {"code": self.code, "message": self.message}
        if self.details is not None:
            error["details"] = [d.as_dict() for d in self.details]
        error.update(self.extra)
        return {"error": error}


def malformed_json(reason: str) -> ApiError:
    return ApiError(400, "malformed_json", reason)


def payload_too_large(limit: int) -> ApiError:
    return ApiError(413, "payload_too_large", f"Request body exceeds the {limit} byte limit.")


def unsupported_media_type() -> ApiError:
    return ApiError(415, "unsupported_media_type", "Content-Type must be application/json (UTF-8).")


def unsupported_schema_version(version: str, supported: list[str]) -> ApiError:
    shown = version if len(version) <= 64 else version[:64] + "..."
    return ApiError(
        400,
        "unsupported_schema_version",
        f"Schema version '{shown}' is not supported.",
        extra={"supported_versions": supported},
    )


def validation_error(details: list[ErrorDetail]) -> ApiError:
    return ApiError(422, "validation_error", "Telemetry envelope failed validation.", details=details)


def invalid_request(details: list[ErrorDetail]) -> ApiError:
    """A read-API request whose parameters are invalid (not an envelope: see validation_error)."""
    return ApiError(422, "invalid_request", "One or more request parameters are invalid.", details=details)


def host_not_found(host_id: str) -> ApiError:
    shown = host_id if len(host_id) <= 64 else host_id[:64] + "..."
    return ApiError(404, "host_not_found", f"No telemetry has been received for host '{shown}'.")


def unauthorized() -> ApiError:
    """Missing, malformed or unknown bearer token. One fixed message: never says which part was wrong."""
    return ApiError(401, "unauthorized", "A valid agent token is required.", headers={"WWW-Authenticate": "Bearer"})


def session_required() -> ApiError:
    """Browser API: missing, malformed, expired or untrustworthy session token. One fixed message."""
    return ApiError(401, "unauthorized", "A valid session token is required.", headers={"WWW-Authenticate": "Bearer"})


def not_authorized() -> ApiError:
    """A valid session, but the account is not on this deployment's allowlist."""
    return ApiError(403, "forbidden", "This account is not authorized to view this workspace.")


def auth_unavailable() -> ApiError:
    return ApiError(503, "auth_unavailable", "Sessions cannot be verified right now. Retry shortly.")


def forbidden() -> ApiError:
    """Valid token, but the envelope's agent_id / host_id is outside the credential's scope."""
    return ApiError(403, "forbidden", "This credential is not permitted to send telemetry for the given agent_id or host_id.")


def envelope_id_conflict(envelope_id: str) -> ApiError:
    return ApiError(
        409,
        "envelope_id_conflict",
        f"Envelope '{envelope_id}' was already accepted with different content.",
    )


def unavailable() -> ApiError:
    return ApiError(503, "unavailable", "The envelope could not be durably accepted. Retry with the same envelope_id.")


def internal_error() -> ApiError:
    return ApiError(500, "internal_error", "Internal server error.")


_HTTP_ERRORS = {
    404: ("not_found", "The requested resource was not found."),
    405: ("method_not_allowed", "The method is not allowed for this resource."),
}


def http_error(status: int) -> ApiError:
    """Routing-level errors in the contract's error envelope, with fixed, non-revealing messages."""
    code, message = _HTTP_ERRORS.get(status, ("http_error", "The request could not be processed."))
    return ApiError(status, code, message)
