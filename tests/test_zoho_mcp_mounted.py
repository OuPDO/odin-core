"""Zoho tools behind ApiKeyGuard + a real FastMCP streamable-http app (no real network)."""
import contextlib

import httpx
import pytest
import respx
from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from zoho_support import (
    API,
    BOOKS_ORG_ID,
    BOOKS_TOOLS,
    CRM_TOOLS,
    ENV_ADO,
    ENV_BOOKS,
    ENV_BOTH,
    ENV_OM,
    McpSession,
    TokenMock,
    data_calls,
    tool_json,
)

from odin_mcp.http_app import ApiKeyGuard
from odin_mcp.zoho_tools import register_zoho_tools

API_KEY = "mounted-test-key"

COMBOS = [
    ("none", {}, set()),
    ("books-id-only", dict(ENV_BOOKS), set()),
    ("om-only", dict(ENV_OM), CRM_TOOLS),
    ("om-with-books", {**ENV_OM, **ENV_BOOKS}, CRM_TOOLS | BOOKS_TOOLS),
    ("ado-only", dict(ENV_ADO), CRM_TOOLS),
    ("ado-with-books-id", {**ENV_ADO, **ENV_BOOKS}, CRM_TOOLS),
    ("both-no-books-id", {**ENV_OM, **ENV_ADO}, CRM_TOOLS),
    ("both", dict(ENV_BOTH), CRM_TOOLS | BOOKS_TOOLS),
]


def _build(env):
    mcp = FastMCP(
        "zoho-test", streamable_http_path="/",
        transport_security=TransportSecuritySettings(
            enable_dns_rebinding_protection=True, allowed_hosts=["localhost"],
            allowed_origins=["http://localhost"]))
    registered = register_zoho_tools(mcp, env=env)
    return mcp, registered


@contextlib.asynccontextmanager
async def mounted(env):
    mcp, registered = _build(env)
    app = ApiKeyGuard(mcp.streamable_http_app(), API_KEY)
    async with mcp.session_manager.run():
        session = McpSession(app, API_KEY)
        try:
            yield session, registered, app
        finally:
            await session.aclose()


@pytest.fixture
def router():
    with respx.mock(assert_all_called=False) as r:
        yield r


@pytest.mark.parametrize("label,env,expected", COMBOS, ids=[c[0] for c in COMBOS])
async def test_tools_list_matches_credentials(label, env, expected, router):
    async with mounted(env) as (session, registered, _):
        await session.open()
        assert await session.tool_names() == expected
        assert set(registered) == expected
    assert not router.calls


async def test_requires_api_key(router):
    async with mounted(ENV_BOTH) as (_, _, app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                     base_url="http://localhost") as raw:
            body = {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}
            headers = {"Accept": "application/json, text/event-stream"}
            assert (await raw.post("/", json=body, headers=headers)).status_code == 401
            bad = {**headers, "Authorization": "Bearer wrong"}
            assert (await raw.post("/", json=body, headers=bad)).status_code == 401


async def test_tool_schemas_are_read_only_and_documented(router):
    async with mounted(ENV_BOTH) as (session, _, _):
        await session.open()
        resp = await session.rpc("tools/list")
        for tool in resp["result"]["tools"]:
            assert "nur lesend" in tool["description"].lower(), tool["name"]
            assert "organization_id" not in tool["inputSchema"]["properties"]


async def test_crm_search_returns_data_from_mocked_zoho(router):
    TokenMock(router)
    route = router.get(f"{API}/crm/v6/Leads/search").mock(return_value=httpx.Response(200, json={
        "data": [{"id": "1", "Full_Name": "Erika Muster", "$editable": True}],
        "info": {"page": 1, "more_records": False}}))
    async with mounted(ENV_BOTH) as (session, _, _):
        await session.open()
        result = await session.call("zoho_crm_search", {
            "org": "om", "module": "Leads", "by": "full_name", "value": "Erika Muster"})
    assert tool_json(result) == {"data": [{"id": "1", "Full_Name": "Erika Muster"}],
                                 "page": 1, "more_records": False}
    assert route.calls.last.request.url.params["criteria"] == "(Full_Name:equals:Erika Muster)"


async def test_each_tool_returns_data(router):
    TokenMock(router)
    router.get(f"{API}/crm/v6/Contacts/5").mock(
        return_value=httpx.Response(200, json={"data": [{"id": "5"}]}))
    router.get(f"{API}/crm/v6/Contacts").mock(
        return_value=httpx.Response(200, json={"data": [{"id": "6"}], "info": {"page": 1}}))
    router.get(f"{API}/crm/v6/Contacts/5/Notes").mock(
        return_value=httpx.Response(200, json={"data": [{"id": "n"}], "info": {"page": 1}}))
    router.get(f"{API}/books/v3/contacts").mock(
        return_value=httpx.Response(200, json={"contacts": [{"contact_id": "c"}]}))
    router.get(f"{API}/books/v3/contacts/8").mock(
        return_value=httpx.Response(200, json={"contact": {"contact_id": "8"}}))
    router.get(f"{API}/books/v3/estimates").mock(
        return_value=httpx.Response(200, json={"estimates": [{"estimate_id": "e"}]}))
    router.get(f"{API}/books/v3/estimates/9").mock(
        return_value=httpx.Response(200, json={"estimate": {"estimate_id": "9"}}))
    calls = [
        ("zoho_crm_get", {"org": "ado", "module": "Contacts", "id": "5"}),
        ("zoho_crm_list", {"org": "ado", "module": "Contacts", "fields": ["Full_Name"]}),
        ("zoho_crm_list_notes", {"org": "ado", "module": "Contacts", "id": "5"}),
        ("zoho_books_search_contacts", {"text": "x"}),
        ("zoho_books_get_contact", {"id": "8"}),
        ("zoho_books_list_estimates", {"status": "sent"}),
        ("zoho_books_get_estimate", {"id": "9"}),
    ]
    async with mounted(ENV_BOTH) as (session, _, _):
        await session.open()
        for name, args in calls:
            payload = tool_json(await session.call(name, args))
            assert payload["data"], name
    books_reqs = [r for r in data_calls(router) if "/books/" in str(r.url)]
    assert books_reqs and all(r.url.params["organization_id"] == BOOKS_ORG_ID for r in books_reqs)


@pytest.mark.parametrize("name,args", [
    ("zoho_crm_get", {"org": "om", "module": "Leads", "id": "1/../2"}),
    ("zoho_crm_get", {"org": "om", "module": "Users", "id": "1"}),
    ("zoho_crm_get", {"org": "evil", "module": "Leads", "id": "1"}),
    ("zoho_crm_search", {"org": "om", "module": "Leads", "by": "account", "value": "x"}),
    ("zoho_crm_search", {"org": "om", "module": "Leads", "by": "word", "value": "x", "page": 99}),
    ("zoho_crm_list", {"org": "om", "module": "Leads", "fields": ["a,b"]}),
    ("zoho_crm_list_notes", {"org": "om", "module": "Leads", "id": "%2F"}),
    ("zoho_books_get_contact", {"id": "1?x=1"}),
    ("zoho_books_list_estimates", {"status": "x&y=1"}),
    ("zoho_books_search_contacts", {"text": "x", "page": 0}),
    ("zoho_books_get_estimate", {"id": "abc"}),
])
async def test_rejected_input_is_tool_error_with_zero_upstream_calls(name, args, router):
    token = TokenMock(router)
    async with mounted(ENV_BOTH) as (session, _, _):
        await session.open()
        result = await session.call(name, args)
    assert result["isError"] is True
    assert token.calls == 0 and not data_calls(router)


async def test_missing_argument_is_tool_error(router):
    token = TokenMock(router)
    async with mounted(ENV_BOTH) as (session, _, _):
        await session.open()
        result = await session.call("zoho_crm_get", {"org": "om", "module": "Leads"})
    assert result["isError"] is True
    assert token.calls == 0 and not data_calls(router)


async def test_caller_supplied_books_organization_never_reaches_zoho(router):
    TokenMock(router)
    route = router.get(f"{API}/books/v3/contacts/1").mock(
        return_value=httpx.Response(200, json={"contact": {"contact_id": "1"}}))
    async with mounted(ENV_BOTH) as (session, _, _):
        await session.open()
        await session.call("zoho_books_get_contact", {"id": "1", "organization_id": "999"})
    for call in route.calls:
        assert call.request.url.params["organization_id"] == BOOKS_ORG_ID


async def test_ado_only_rejects_om_org(router):
    token = TokenMock(router)
    async with mounted(ENV_ADO) as (session, _, _):
        await session.open()
        result = await session.call("zoho_crm_get", {"org": "om", "module": "Leads", "id": "1"})
    assert result["isError"] is True
    assert token.calls == 0 and not data_calls(router)


async def test_om_only_rejects_ado_org(router):
    token = TokenMock(router)
    async with mounted(ENV_OM) as (session, _, _):
        await session.open()
        result = await session.call("zoho_crm_get", {"org": "ado", "module": "Leads", "id": "1"})
    assert result["isError"] is True
    assert token.calls == 0 and not data_calls(router)


async def test_upstream_failure_is_tool_error_without_secrets(router):
    TokenMock(router)
    router.get(f"{API}/crm/v6/Leads/1").mock(return_value=httpx.Response(
        500, json={"message": "boom " + ENV_OM["ZOHO_OM_CLIENT_SECRET"]}))
    async with mounted(ENV_BOTH) as (session, _, _):
        await session.open()
        result = await session.call("zoho_crm_get", {"org": "om", "module": "Leads", "id": "1"})
    assert result["isError"] is True
    text = result["content"][0]["text"]
    for secret in (ENV_OM["ZOHO_OM_CLIENT_SECRET"], ENV_OM["ZOHO_OM_REFRESH_TOKEN"], "AT-om-"):
        assert secret not in text


def test_invalid_config_registers_nothing_and_does_not_raise():
    mcp = FastMCP("zoho-test")
    bad = {**ENV_OM, "ZOHO_API_DOMAIN": "http://evil.example"}
    assert register_zoho_tools(mcp, env=bad) == []


async def test_real_server_registers_no_zoho_tools_without_credentials(monkeypatch):
    for key in list(ENV_BOTH) + ["ZOHO_ACCOUNTS_URL", "ZOHO_API_DOMAIN"]:
        monkeypatch.delenv(key, raising=False)
    import odin_mcp.server as server
    names = {t.name for t in await server.mcp.list_tools()}
    assert not {n for n in names if n.startswith("zoho_")}
    assert "search_knowledge" in names
