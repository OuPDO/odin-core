"""Zoho HTTP client (CRM v6, Books v3): reads and a fixed set of writes.

Hard guarantees, enforced before any network I/O:

* Data requests must match a fixed allowlist of (method, anchored path pattern, query keys,
  body shape) entries (validation.py, write_rules.py). Reads are GET; writes are POST/PUT
  on a few exact paths. There are no deletes and no mail, reminder or portal routes: a
  secondary denylist rejects those even if the allowlist were widened.
* A write is sent once. After a network error or timeout it is never retried (the outcome is
  unknown); only a 401 INVALID_TOKEN (request rejected, not executed) is retried once.
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
    ZohoOutcomeUnknown,
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
    "ZohoOutcomeUnknown",
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


OK_STATUSES = {"GET": (200, 204), "POST": (200, 201, 202, 204), "PUT": (200, 201, 202, 204)}


def _is_rejection_envelope(parsed: Any, books: bool) -> bool:
    if not isinstance(parsed, dict):
        return False
    if books:
        code = parsed.get("code")
        return isinstance(code, int) and not isinstance(code, bool)
    data = parsed.get("data")
    return isinstance(data, list) and bool(data) and all(isinstance(i, dict) for i in data)


class ZohoClient:
    """Zoho client with per-org token caches; only allowlisted requests can be sent."""

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
        return await self._execute(org, endpoint, path, query, None)

    async def write(self, org: str, method: str, path: str, body: Any) -> dict | None:
        """Validated POST/PUT (allowlisted paths and body shapes only). Never retried after a
        network error or timeout; the error text then says the outcome is unknown."""
        endpoint = validate_request(method, path, {}, body)
        return await self._execute(org, endpoint, path, {}, body)

    async def _execute(self, org: str, endpoint: Endpoint, path: str, query: dict,
                       body: Any) -> dict | None:
        org = self._resolve_org(org, endpoint)
        if endpoint.books:
            query["organization_id"] = self._config.books_organization_id or ""
        try:
            async with asyncio.timeout(OVERALL_TIMEOUT_SECONDS):
                return await self._with_retry(org, endpoint, path, query, body)
        except TimeoutError:
            if endpoint.method == "GET":
                raise ZohoAPIError("Zoho request timed out") from None
            raise ZohoOutcomeUnknown("Zoho request timed out") from None

    async def _with_retry(self, org: str, endpoint: Endpoint, path: str, query: dict,
                          body: Any) -> dict | None:
        token = await self._tokens.get(org)
        status, body_bytes = await self._send(token, endpoint.method, path, query, body)
        if self._token_rejected(status, body_bytes, endpoint) and not self._tokens.rejected(
                org, token):
            # not a failed replacement: mint one fresh token and retry once
            token = await self._tokens.get(org)
            status, body_bytes = await self._send(token, endpoint.method, path, query, body)
            if self._token_rejected(status, body_bytes, endpoint):
                # the retry token is rejected too: evict it and stop hammering Zoho
                self._tokens.rejected(org, token)
                self._tokens.cool_down(org)
        if status in OK_STATUSES[endpoint.method]:
            self._tokens.confirmed(org, token)
        if endpoint.method != "GET":
            return self._parse_write(status, body_bytes, endpoint.books)
        return self._parse(status, body_bytes)

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

    async def _send(self, token: str, method: str, path: str, query: dict,
                    body: Any) -> tuple[int, bytes]:
        headers = {"Authorization": f"Zoho-oauthtoken {token}", "Accept": "application/json"}
        extra = {} if body is None else {"json": body}
        try:
            async with self._http() as http:
                return await fetch(http, method, self._config.api_domain + path, "request",
                                   params=query, headers=headers, **extra)
        except ZohoAPIError as exc:
            if method == "GET":
                raise
            raise ZohoOutcomeUnknown(str(exc)) from None

    def _http(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(timeout=REQUEST_TIMEOUT_SECONDS, transport=self._transport,
                                 follow_redirects=False)

    @staticmethod
    def _parse_write(status: int, body: bytes, books: bool) -> dict:
        """2xx with a JSON object, or a 400 that carries Zoho's per-record / numeric-code
        rejection envelope, is returned for projection. Other 4xx are confirmed rejections.
        Everything else (5xx, odd statuses, unreadable 2xx) leaves the outcome unknown."""
        parsed = parse_json(body)
        if 200 <= status < 300:
            if status == 204 or not isinstance(parsed, dict):
                raise ZohoOutcomeUnknown("unreadable response")
            return ZohoClient._strip(parsed, ZohoOutcomeUnknown("unreadable response"))
        if status == 400 and _is_rejection_envelope(parsed, books):
            return ZohoClient._strip(parsed, ZohoOutcomeUnknown("unreadable response"))
        if 400 <= status < 500:
            code = safe_code(parsed.get("code") if isinstance(parsed, dict) else None)
            raise ZohoAPIError(f"Zoho API returned HTTP {status}, {code}")
        raise ZohoOutcomeUnknown(f"HTTP {status}")

    @staticmethod
    def _strip(parsed: dict, error: ZohoAPIError) -> dict:
        try:
            return strip_dollar_keys(parsed)
        except RecursionError:
            raise error from None

    @staticmethod
    def _parse(status: int, body: bytes, method: str = "GET") -> dict | None:
        if status == 204:
            return None
        parsed = parse_json(body)
        if status not in OK_STATUSES[method]:
            code = safe_code(parsed.get("code") if isinstance(parsed, dict) else None)
            raise ZohoAPIError(f"Zoho API returned HTTP {status}, {code}")
        if not isinstance(parsed, dict):
            raise ZohoAPIError("Zoho API returned an unreadable response")
        try:
            return strip_dollar_keys(parsed)
        except RecursionError:
            raise ZohoAPIError("Zoho API returned an unreadable response") from None
