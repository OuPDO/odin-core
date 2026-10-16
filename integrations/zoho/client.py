"""Read-only Zoho HTTP client (CRM v6, Books v3).

Hard guarantees, enforced before any network I/O:

* Data requests are GET only and must match a fixed allowlist of anchored path
  patterns with fixed, validated query parameters per endpoint (validation.py).
  Paths are built by the callers from enum modules and digit-only ids.
* The only non-GET call is the OAuth token mint (`grant_type=refresh_token`),
  hardcoded to `{accounts_url}/oauth/v2/token` (tokens.py). EU hosts only.
* Books always runs as org `om` with the organization id from the environment.
* Each call has an overall 30 s deadline and a 5 MB response cap.
* Error texts and logs never carry tokens, secrets, upstream text or request values.
"""
import asyncio
from collections.abc import Mapping
from typing import Any

import httpx

from integrations.zoho.config import BOOKS_ORG, ORGS, OrgCredentials, ZohoConfig
from integrations.zoho.errors import (
    ZohoAPIError,
    ZohoError,
    ZohoRequestError,
    safe_code,
)
from integrations.zoho.httpio import fetch, parse_json, silence_http_loggers
from integrations.zoho.tokens import TokenManager
from integrations.zoho.validation import (
    BOOKS_MAX_PAGE,
    CRM_MAX_PAGE,
    PER_PAGE,
    Endpoint,
    validate_id,
    validate_page,
    validate_request,
)

__all__ = [
    "BOOKS_MAX_PAGE",
    "BOOKS_ORG",
    "CRM_MAX_PAGE",
    "ORGS",
    "OVERALL_TIMEOUT_SECONDS",
    "PER_PAGE",
    "OrgCredentials",
    "ZohoAPIError",
    "ZohoClient",
    "ZohoConfig",
    "ZohoError",
    "ZohoRequestError",
    "strip_dollar_keys",
    "validate_id",
    "validate_page",
    "validate_request",
]

silence_http_loggers()

REQUEST_TIMEOUT_SECONDS = 20.0
OVERALL_TIMEOUT_SECONDS = 30
CRM_INVALID_TOKEN_CODES = frozenset({"INVALID_TOKEN", "INVALID_OAUTHTOKEN"})
BOOKS_INVALID_TOKEN_CODES = frozenset({14, 57})


def strip_dollar_keys(node: Any) -> Any:
    """Recursively drop keys starting with '$' (Zoho internal metadata)."""
    if isinstance(node, dict):
        return {k: strip_dollar_keys(v) for k, v in node.items()
                if not (isinstance(k, str) and k.startswith("$"))}
    if isinstance(node, list):
        return [strip_dollar_keys(item) for item in node]
    return node


def _is_invalid_token(body: bytes, books: bool) -> bool:
    """True only for a 401 body that says the access token itself is bad."""
    parsed = parse_json(body)
    code = parsed.get("code") if isinstance(parsed, dict) else None
    if isinstance(code, bool):
        return False
    if books:
        return isinstance(code, int) and code in BOOKS_INVALID_TOKEN_CODES
    return isinstance(code, str) and code in CRM_INVALID_TOKEN_CODES


class ZohoClient:
    """GET-only Zoho client with per-org token caches. No write verbs exist on purpose."""

    def __init__(self, config: ZohoConfig, *, transport: httpx.AsyncBaseTransport | None = None):
        silence_http_loggers()
        self._config = config
        self._transport = transport
        self._tokens = TokenManager(config, self._http)

    @property
    def config(self) -> ZohoConfig:
        return self._config

    async def get(self, org: str, path: str,
                  params: Mapping[str, str | int] | None = None) -> dict | None:
        """Validated GET against the Zoho API. Returns sanitized JSON (dict) or None (204)."""
        query: dict[str, str | int] = dict(params or {})
        endpoint = validate_request("GET", path, query)
        org = self._resolve_org(org, endpoint)
        if endpoint.books:
            query["organization_id"] = self._config.books_organization_id or ""
        try:
            async with asyncio.timeout(OVERALL_TIMEOUT_SECONDS):
                return await self._get_with_retry(org, endpoint, path, query)
        except TimeoutError:
            raise ZohoAPIError("Zoho request timed out") from None

    async def _get_with_retry(self, org: str, endpoint: Endpoint, path: str,
                              query: dict) -> dict | None:
        token = await self._tokens.get(org)
        status, body = await self._send(token, path, query)
        if self._token_rejected(status, body, endpoint) and not self._tokens.rejected(org, token):
            # not a failed replacement: mint one fresh token and retry once
            token = await self._tokens.get(org)
            status, body = await self._send(token, path, query)
            if self._token_rejected(status, body, endpoint):
                # the retry token is rejected too: evict it and stop hammering Zoho
                self._tokens.rejected(org, token)
                self._tokens.cool_down(org)
        if status in (200, 204):
            self._tokens.confirmed(org)
        return self._parse(status, body)

    @staticmethod
    def _token_rejected(status: int, body: bytes, endpoint: Endpoint) -> bool:
        return status == 401 and _is_invalid_token(body, endpoint.books)

    def _resolve_org(self, org: str, endpoint: Endpoint) -> str:
        if endpoint.books:
            if org != BOOKS_ORG or not self._config.books_enabled:
                raise ZohoRequestError("Books is not available for this org")
            return BOOKS_ORG
        if org not in self._config.orgs:
            raise ZohoRequestError(
                f"org not configured; available: {', '.join(self._config.crm_orgs) or 'none'}")
        return org

    async def _send(self, token: str, path: str, query: dict) -> tuple[int, bytes]:
        headers = {"Authorization": f"Zoho-oauthtoken {token}", "Accept": "application/json"}
        async with self._http() as http:
            return await fetch(http, "GET", self._config.api_domain + path, "request",
                               params=query, headers=headers)

    def _http(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(timeout=REQUEST_TIMEOUT_SECONDS, transport=self._transport,
                                 follow_redirects=False)

    @staticmethod
    def _parse(status: int, body: bytes) -> dict | None:
        if status == 204:
            return None
        parsed = parse_json(body)
        if status != 200:
            code = safe_code(parsed.get("code") if isinstance(parsed, dict) else None)
            raise ZohoAPIError(f"Zoho API returned HTTP {status}, {code}")
        if not isinstance(parsed, dict):
            raise ZohoAPIError("Zoho API returned an unreadable response")
        try:
            return strip_dollar_keys(parsed)
        except RecursionError:
            raise ZohoAPIError("Zoho API returned an unreadable response") from None
