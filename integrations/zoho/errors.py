"""Zoho exceptions and the fixed allowlist of upstream error codes.

Upstream error text is never passed through: Zoho (or anything answering in its place)
could echo secrets or personal data. Only codes from a fixed set, or small integers
(Books), are shown; everything else becomes `upstream_error`.
"""
from typing import Any

FALLBACK_CODE = "upstream_error"
MAX_NUMERIC_CODE = 99999

ALLOWED_CODES = frozenset({
    # CRM / Books REST API
    "INVALID_TOKEN", "INVALID_OAUTHTOKEN", "OAUTH_SCOPE_MISMATCH", "NO_PERMISSION",
    "INVALID_DATA", "REQUIRED_PARAM_MISSING", "INVALID_URL_PATTERN", "LIMIT_REACHED",
    "INVALID_REQUEST", "INVALID_MODULE", "AUTHENTICATION_FAILURE", "TOO_MANY_REQUESTS",
    "INTERNAL_ERROR",
    # OAuth token endpoint
    "invalid_client", "invalid_code", "invalid_request", "invalid_grant", "access_denied",
})


class ZohoError(Exception):
    """Base class; messages are safe to show to MCP callers."""


class ZohoRequestError(ZohoError, ValueError):
    """Request rejected locally, before any network call."""


class ZohoAPIError(ZohoError):
    """Zoho (or the network) failed; text is sanitized."""


def safe_code(raw: Any) -> str:
    """Map an upstream error code to the allowlist, else to `upstream_error`."""
    if isinstance(raw, bool):
        return FALLBACK_CODE
    if isinstance(raw, int) and 0 <= raw <= MAX_NUMERIC_CODE:
        return str(raw)
    if isinstance(raw, str) and raw in ALLOWED_CODES:
        return raw
    return FALLBACK_CODE
