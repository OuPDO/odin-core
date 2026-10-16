"""Read-only Zoho HTTP client (CRM v6, Books v3).

Hard guarantees, enforced before any network I/O:

* Data requests are GET only and must match a fixed allowlist of anchored path
  patterns with a fixed set of query keys per endpoint. Paths are built by the
  callers from enum modules and digit-only ids; nothing free-form reaches the URL.
* The only non-GET call is the OAuth token mint (`grant_type=refresh_token`),
  hardcoded to `{accounts_url}/oauth/v2/token`.
* Tokens are cached per org (50 minutes). Books always runs as org `om` with the
  organization id from the environment, never from the caller.
* Error texts and logs never carry access tokens, refresh tokens or client secrets.
"""
import logging
import re
import time
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

import httpx

log = logging.getLogger(__name__)

DEFAULT_ACCOUNTS_URL = "https://accounts.zoho.eu"
DEFAULT_API_DOMAIN = "https://www.zohoapis.eu"
TOKEN_PATH = "/oauth/v2/token"
TOKEN_TTL_SECONDS = 50 * 60
REQUEST_TIMEOUT_SECONDS = 20.0
PER_PAGE = 50
CRM_MAX_PAGE = 40
BOOKS_MAX_PAGE = 100
MAX_TEXT_LENGTH = 200
BOOKS_ORG = "om"
ORGS = ("om", "ado")

_ID_RE = re.compile(r"[0-9]{1,25}")
_BASE_URL_RE = re.compile(r"https://[A-Za-z0-9.-]+(?::[0-9]{1,5})?")
_ERROR_CODE_RE = re.compile(r"[A-Za-z0-9_.-]{1,60}")
_MODULES = "Leads|Contacts|Deals|Accounts"
_ID = "[0-9]{1,25}"


class ZohoError(Exception):
    """Base class; messages are safe to show to MCP callers."""


class ZohoRequestError(ZohoError, ValueError):
    """Request rejected locally, before any network call."""


class ZohoAPIError(ZohoError):
    """Zoho (or the network) failed; text is sanitized."""


@dataclass(frozen=True)
class Endpoint:
    pattern: re.Pattern
    query_keys: frozenset
    books: bool = False


def _ep(pattern: str, keys: tuple = (), books: bool = False) -> Endpoint:
    return Endpoint(re.compile(pattern), frozenset(keys), books)


_SEARCH_KEYS = ("word", "email", "phone", "criteria", "page", "per_page")
ALLOWED_ENDPOINTS = (
    _ep(rf"/crm/v6/(?:{_MODULES})/search", _SEARCH_KEYS),
    _ep(rf"/crm/v6/(?:{_MODULES})", ("fields", "page", "per_page")),
    _ep(rf"/crm/v6/(?:{_MODULES})/{_ID}"),
    _ep(rf"/crm/v6/(?:{_MODULES})/{_ID}/Notes", ("page", "per_page")),
    _ep("/books/v3/contacts", ("search_text", "page", "per_page"), books=True),
    _ep(rf"/books/v3/contacts/{_ID}", books=True),
    _ep("/books/v3/estimates", ("status", "customer_id", "page", "per_page"), books=True),
    _ep(rf"/books/v3/estimates/{_ID}", books=True),
)


def validate_request(method: str, path: str, params: Mapping[str, Any]) -> Endpoint:
    """Raise ZohoRequestError unless (method, path, query keys) is on the allowlist."""
    if method != "GET":
        raise ZohoRequestError("only GET requests are allowed")
    if not isinstance(path, str):
        raise ZohoRequestError("path not allowed")
    for endpoint in ALLOWED_ENDPOINTS:
        if endpoint.pattern.fullmatch(path):
            break
    else:
        raise ZohoRequestError("path not allowed")
    for key, value in params.items():
        if key not in endpoint.query_keys:
            raise ZohoRequestError(f"query parameter not allowed: {key}")
        if isinstance(value, bool) or not isinstance(value, (str, int)):
            raise ZohoRequestError(f"query parameter has invalid type: {key}")
    return endpoint


def validate_id(value: Any, what: str = "id") -> str:
    if not isinstance(value, str) or not _ID_RE.fullmatch(value):
        raise ZohoRequestError(f"{what} must be 1 to 25 digits")
    return value


def validate_page(value: Any, max_page: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= max_page:
        raise ZohoRequestError(f"page must be an integer from 1 to {max_page}")
    return value


def validate_text(value: Any, what: str = "value") -> str:
    ok = isinstance(value, str) and 1 <= len(value) <= MAX_TEXT_LENGTH
    if not ok or not value.strip() or any(ord(ch) < 32 or ord(ch) == 127 for ch in value):
        raise ZohoRequestError(f"{what} must be 1 to {MAX_TEXT_LENGTH} printable characters")
    return value


def strip_dollar_keys(node: Any) -> Any:
    """Recursively drop keys starting with '$' (Zoho internal metadata)."""
    if isinstance(node, dict):
        return {k: strip_dollar_keys(v) for k, v in node.items()
                if not (isinstance(k, str) and k.startswith("$"))}
    if isinstance(node, list):
        return [strip_dollar_keys(item) for item in node]
    return node


@dataclass(frozen=True)
class OrgCredentials:
    client_id: str = field(repr=False)
    client_secret: str = field(repr=False)
    refresh_token: str = field(repr=False)


def _base_url(raw: str, name: str) -> str:
    url = raw.strip().rstrip("/")
    if not _BASE_URL_RE.fullmatch(url):
        raise ValueError(f"{name} must be a plain https origin")
    return url


@dataclass(frozen=True)
class ZohoConfig:
    accounts_url: str
    api_domain: str
    orgs: Mapping[str, OrgCredentials]
    books_organization_id: str | None = None

    @classmethod
    def from_env(cls, env: Mapping[str, str]) -> "ZohoConfig":
        orgs = {}
        for org in ORGS:
            prefix = f"ZOHO_{org.upper()}_"
            values = [env.get(prefix + key, "").strip()
                      for key in ("CLIENT_ID", "CLIENT_SECRET", "REFRESH_TOKEN")]
            if all(values):
                orgs[org] = OrgCredentials(*values)
            elif any(values):
                log.warning("Zoho org %s has incomplete credentials; org disabled", org)
        books_id = env.get("ZOHO_BOOKS_ORGANIZATION_ID", "").strip()
        return cls(
            accounts_url=_base_url(env.get("ZOHO_ACCOUNTS_URL") or DEFAULT_ACCOUNTS_URL,
                                   "ZOHO_ACCOUNTS_URL"),
            api_domain=_base_url(env.get("ZOHO_API_DOMAIN") or DEFAULT_API_DOMAIN,
                                 "ZOHO_API_DOMAIN"),
            orgs=orgs,
            books_organization_id=books_id if _ID_RE.fullmatch(books_id) else None,
        )

    @property
    def crm_orgs(self) -> tuple[str, ...]:
        return tuple(org for org in ORGS if org in self.orgs)

    @property
    def books_enabled(self) -> bool:
        return BOOKS_ORG in self.orgs and self.books_organization_id is not None


class ZohoClient:
    """GET-only Zoho client with per-org token caches. No write verbs exist on purpose."""

    def __init__(self, config: ZohoConfig, *, transport: httpx.AsyncBaseTransport | None = None):
        self._config = config
        self._transport = transport
        self._tokens: dict[str, tuple[str, float]] = {}

    @property
    def config(self) -> ZohoConfig:
        return self._config

    async def get(self, org: str, path: str, params: Mapping[str, Any] | None = None):
        """Validated GET against the Zoho API. Returns parsed JSON (sanitized) or None (204)."""
        query = dict(params or {})
        endpoint = validate_request("GET", path, query)
        org = self._resolve_org(org, endpoint)
        if endpoint.books:
            query["organization_id"] = self._config.books_organization_id
        response = await self._send(org, path, query)
        if response.status_code == 401:
            self._tokens.pop(org, None)
            response = await self._send(org, path, query)
        return self._parse(response)

    def _resolve_org(self, org: str, endpoint: Endpoint) -> str:
        if endpoint.books:
            if org != BOOKS_ORG or not self._config.books_enabled:
                raise ZohoRequestError("Books is not available for this org")
            return BOOKS_ORG
        if org not in self._config.orgs:
            raise ZohoRequestError(
                f"org not configured; available: {', '.join(self._config.crm_orgs) or 'none'}")
        return org

    async def _send(self, org: str, path: str, query: dict) -> httpx.Response:
        token = await self._token(org)
        headers = {"Authorization": f"Zoho-oauthtoken {token}", "Accept": "application/json"}
        try:
            async with self._http() as http:
                return await http.get(self._config.api_domain + path, params=query,
                                      headers=headers)
        except httpx.HTTPError as exc:
            raise ZohoAPIError(f"Zoho request failed ({type(exc).__name__})") from None

    def _http(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(timeout=REQUEST_TIMEOUT_SECONDS, transport=self._transport,
                                 follow_redirects=False)

    async def _token(self, org: str) -> str:
        cached = self._tokens.get(org)
        if cached and time.monotonic() < cached[1]:
            return cached[0]
        token = await self._mint(org)
        self._tokens[org] = (token, time.monotonic() + TOKEN_TTL_SECONDS)
        return token

    async def _mint(self, org: str) -> str:
        creds = self._config.orgs[org]
        form = {"grant_type": "refresh_token", "client_id": creds.client_id,
                "client_secret": creds.client_secret, "refresh_token": creds.refresh_token}
        try:
            async with self._http() as http:
                response = await http.post(self._config.accounts_url + TOKEN_PATH, data=form)
        except httpx.HTTPError as exc:
            raise ZohoAPIError(f"Zoho token refresh failed ({type(exc).__name__})") from None
        token = _json_or_none(response)
        if response.status_code == 200 and isinstance(token, dict) and token.get("access_token"):
            return str(token["access_token"])
        code = _safe_code(token.get("error") if isinstance(token, dict) else None)
        raise ZohoAPIError(f"Zoho token refresh failed (HTTP {response.status_code}{code})")

    def _parse(self, response: httpx.Response):
        if response.status_code == 204:
            return None
        if response.status_code != 200:
            body = _json_or_none(response)
            code = _safe_code(body.get("code") if isinstance(body, dict) else None)
            raise ZohoAPIError(f"Zoho API returned HTTP {response.status_code}{code}")
        body = _json_or_none(response)
        if body is None:
            raise ZohoAPIError("Zoho API returned an unreadable response")
        return strip_dollar_keys(body)


def _json_or_none(response: httpx.Response):
    try:
        return response.json()
    except ValueError:
        return None


def _safe_code(raw: Any) -> str:
    """Only a short identifier-like error code is passed on, never free text from Zoho."""
    if isinstance(raw, (str, int)) and not isinstance(raw, bool):
        text = str(raw)
        if _ERROR_CODE_RE.fullmatch(text):
            return f", {text}"
    return ""
