"""OAuth access tokens per org: single flight, TTL from expires_in, failure backoff."""
import asyncio
import logging
import time
from collections.abc import Callable
from typing import Any

import httpx

from integrations.zoho.config import ORGS, ZohoConfig
from integrations.zoho.errors import ZohoAPIError, safe_code
from integrations.zoho.httpio import fetch, parse_json

log = logging.getLogger("odin.integrations.zoho")

TOKEN_PATH = "/oauth/v2/token"
MAX_TTL_SECONDS = 50 * 60
EXPIRY_MARGIN_SECONDS = 300
DEFAULT_EXPIRES_IN = 3600
MINT_BACKOFF_SECONDS = 60


def ttl_for(expires_in: Any) -> float:
    """Cache lifetime: min(50 min, expires_in - 5 min); a bogus value counts as 3600 s."""
    valid = isinstance(expires_in, (int, float)) and not isinstance(expires_in, bool)
    seconds = expires_in if valid else DEFAULT_EXPIRES_IN
    return max(0.0, min(MAX_TTL_SECONDS, seconds - EXPIRY_MARGIN_SECONDS))


class TokenManager:
    """Access tokens per org. One mint at a time per org; a failed mint blocks that org for 60 s."""

    def __init__(self, config: ZohoConfig, http_factory: Callable[[], httpx.AsyncClient]):
        self._config = config
        self._http = http_factory
        self._cache: dict[str, tuple[str, float]] = {}
        self._blocked_until: dict[str, float] = {}
        self._replacement: dict[str, str] = {}  # org -> token minted after an unconfirmed rejection
        self._pending: dict[str, int] = {}  # org -> rejections since the last good response
        self._locks = {org: asyncio.Lock() for org in ORGS}

    async def get(self, org: str) -> str:
        """Cached token, or mint one."""
        cached = self._fresh(org)
        if cached:
            return cached
        async with self._locks[org]:
            cached = self._fresh(org)  # another caller may have minted while we waited
            if cached:
                return cached
            if time.monotonic() < self._blocked_until.get(org, 0.0):
                raise ZohoAPIError("Zoho token refresh is backing off after a failure")
            return await self._refresh(org)

    def invalidate(self, org: str, failed_token: str) -> None:
        """Evict only if the cache still holds the token that was rejected."""
        cached = self._cache.get(org)
        if cached and cached[0] == failed_token:
            del self._cache[org]

    def rejected(self, org: str, token: str) -> bool:
        """Zoho rejected `token`. Evict it; if it was itself a replacement for an earlier
        rejected token, start the cooldown and return True (do not mint again)."""
        self.invalidate(org, token)
        self._pending[org] = self._pending.get(org, 0) + 1
        if self._replacement.get(org) == token:
            self.cool_down(org)
            return True
        return False

    def confirmed(self, org: str) -> None:
        """Zoho accepted a token: earlier rejections were a one-off (e.g. early revocation)."""
        self._pending.pop(org, None)
        self._replacement.pop(org, None)

    def cool_down(self, org: str) -> None:
        self._blocked_until[org] = time.monotonic() + MINT_BACKOFF_SECONDS

    def _fresh(self, org: str) -> str | None:
        cached = self._cache.get(org)
        if cached and time.monotonic() < cached[1]:
            return cached[0]
        return None

    async def _refresh(self, org: str) -> str:
        try:
            token, ttl = await self._mint(org)
        except ZohoAPIError:
            self.cool_down(org)
            raise
        self._blocked_until.pop(org, None)
        if self._pending.get(org):
            self._replacement[org] = token
        self._cache[org] = (token, time.monotonic() + ttl)
        return token

    async def _mint(self, org: str) -> tuple[str, float]:
        creds = self._config.orgs[org]
        form = {"grant_type": "refresh_token", "client_id": creds.client_id,
                "client_secret": creds.client_secret, "refresh_token": creds.refresh_token}
        async with self._http() as http:
            status, body = await fetch(http, "POST", self._config.accounts_url + TOKEN_PATH,
                                       "token refresh", data=form)
        parsed = parse_json(body)
        token = parsed.get("access_token") if isinstance(parsed, dict) else None
        if status == 200 and isinstance(token, str) and token:
            return token, ttl_for(parsed.get("expires_in"))
        code = safe_code(parsed.get("error") if isinstance(parsed, dict) else None)
        raise ZohoAPIError(f"Zoho token refresh failed (HTTP {status}, {code})")
