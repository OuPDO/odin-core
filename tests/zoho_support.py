"""Shared helpers for the Zoho tests: fake env, token endpoint mock, JSON-RPC session.

Everything here is fake: credentials are made-up strings, Zoho is mocked via respx,
nothing leaves the process.
"""
import asyncio
import json
import time
from urllib.parse import parse_qs

import httpx

from integrations.zoho.validation import MODULE_FIELDS

ACCOUNTS = "https://accounts.zoho.eu"
API = "https://www.zohoapis.eu"
TOKEN_URL = f"{ACCOUNTS}/oauth/v2/token"

LEAD_PARAMS = {"fields": MODULE_FIELDS["Leads"]}
CONTACT_PARAMS = {"fields": MODULE_FIELDS["Contacts"]}

BOOKS_ORG_ID = "900000000123"

ENV_OM = {
    "ZOHO_OM_CLIENT_ID": "1000.OMCLIENTID",
    "ZOHO_OM_CLIENT_SECRET": "om-client-secret-xyz",
    "ZOHO_OM_REFRESH_TOKEN": "1000.om-refresh-token-xyz",
}
ENV_ADO = {
    "ZOHO_ADO_CLIENT_ID": "1000.ADOCLIENTID",
    "ZOHO_ADO_CLIENT_SECRET": "ado-client-secret-xyz",
    "ZOHO_ADO_REFRESH_TOKEN": "1000.ado-refresh-token-xyz",
}
ENV_BOOKS = {"ZOHO_BOOKS_ORGANIZATION_ID": BOOKS_ORG_ID}
ENV_BOTH = {**ENV_OM, **ENV_ADO, **ENV_BOOKS}

SECRETS = [
    ENV_OM["ZOHO_OM_CLIENT_SECRET"], ENV_OM["ZOHO_OM_REFRESH_TOKEN"],
    ENV_ADO["ZOHO_ADO_CLIENT_SECRET"], ENV_ADO["ZOHO_ADO_REFRESH_TOKEN"],
    "AT-om-", "AT-ado-",
]

CRM_TOOLS = {"zoho_crm_search", "zoho_crm_get", "zoho_crm_list", "zoho_crm_list_notes"}
BOOKS_TOOLS = {
    "zoho_books_search_contacts", "zoho_books_get_contact",
    "zoho_books_list_estimates", "zoho_books_get_estimate",
    "zoho_books_list_items", "zoho_books_list_taxes",
}
CRM_WRITE_TOOLS = {
    "zoho_crm_create", "zoho_crm_update", "zoho_crm_add_note", "zoho_crm_create_task",
}
BOOKS_WRITE_TOOLS = {
    "zoho_books_create_contact", "zoho_books_update_contact", "zoho_books_create_estimate",
    "zoho_books_update_estimate", "zoho_books_mark_estimate", "zoho_books_create_item",
}
CRM_ALL = CRM_TOOLS | CRM_WRITE_TOOLS
BOOKS_ALL = BOOKS_TOOLS | BOOKS_WRITE_TOOLS


class TokenMock:
    """Token endpoint: access token is 'AT-<org>-<n>', derived from the refresh token.

    `delay` makes the handler yield to the event loop so concurrent callers really overlap.
    """

    def __init__(self, router, *, delay: float = 0.0, expires_in=3600):
        self.minted: list[str] = []
        self.bodies: list[dict] = []
        self._delay = delay
        self._expires_in = expires_in
        self.route = router.post(TOKEN_URL).mock(side_effect=self._handle)

    async def _handle(self, request):
        if self._delay:
            await asyncio.sleep(self._delay)
        body = {k: v[0] for k, v in parse_qs(request.content.decode()).items()}
        self.bodies.append(body)
        org = "om" if "om-refresh" in body.get("refresh_token", "") else "ado"
        token = f"AT-{org}-{len(self.minted) + 1}"
        self.minted.append(token)
        payload = {"access_token": token}
        if self._expires_in is not None:
            payload["expires_in"] = self._expires_in
        return httpx.Response(200, json=payload)

    @property
    def calls(self) -> int:
        return self.route.call_count


class FakeClock:
    """time.monotonic shifted by an offset the test advances (real time keeps flowing)."""

    def __init__(self, monkeypatch):
        real = time.monotonic
        self.offset = 0.0
        monkeypatch.setattr(time, "monotonic", lambda: real() + self.offset)

    def advance(self, seconds: float) -> None:
        self.offset += seconds


def data_calls(router) -> list:
    """All recorded requests that did not go to the token endpoint."""
    return [c.request for c in router.calls if not str(c.request.url).startswith(TOKEN_URL)]


class McpSession:
    """Minimal streamable-http MCP client over ASGITransport (initialize handshake included)."""

    def __init__(self, app, api_key):
        self._client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://localhost")
        self._headers = {
            "Authorization": f"Bearer {api_key}",
            "Accept": "application/json, text/event-stream",
            "Content-Type": "application/json",
        }
        self._id = 0

    async def post(self, payload, headers=None):
        return await self._client.post("/", json=payload, headers=headers or self._headers)

    async def open(self):
        resp = await self.rpc("initialize", {
            "protocolVersion": "2025-03-26", "capabilities": {},
            "clientInfo": {"name": "pytest", "version": "0"}})
        self._headers["mcp-session-id"] = resp["_session"]
        await self.post({"jsonrpc": "2.0", "method": "notifications/initialized"})
        return resp

    async def rpc(self, method, params=None):
        self._id += 1
        resp = await self.post({"jsonrpc": "2.0", "id": self._id, "method": method,
                                "params": params or {}})
        assert resp.status_code == 200, resp.text
        body = _parse_body(resp)
        body["_session"] = resp.headers.get("mcp-session-id")
        return body

    async def tool_names(self):
        resp = await self.rpc("tools/list")
        return {t["name"] for t in resp["result"]["tools"]}

    async def call(self, name, arguments):
        resp = await self.rpc("tools/call", {"name": name, "arguments": arguments})
        return resp["result"]

    async def aclose(self):
        await self._client.aclose()


def _parse_body(resp) -> dict:
    if "text/event-stream" in resp.headers.get("content-type", ""):
        for line in resp.text.splitlines():
            if line.startswith("data:"):
                return json.loads(line[5:].strip())
        raise AssertionError(f"no data line in SSE body: {resp.text!r}")
    return resp.json()


def tool_json(result) -> dict:
    """Decode the JSON payload of a successful tools/call result."""
    assert not result.get("isError"), result
    return json.loads(result["content"][0]["text"])
