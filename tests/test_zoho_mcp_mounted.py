"""Zoho tools behind ApiKeyGuard + a real FastMCP streamable-http app (no real network)."""
import contextlib
import json
import logging
import os
import subprocess
import sys
from pathlib import Path

import httpx
import pytest
import respx
from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from zoho_support import (
    API,
    BOOKS_ALL,
    BOOKS_ORG_ID,
    CRM_ALL,
    ENV_ADO,
    ENV_BOOKS,
    ENV_BOTH,
    ENV_OM,
    TOKEN_URL,
    McpSession,
    TokenMock,
    data_calls,
    tool_json,
)

from integrations.zoho import crm
from odin_mcp.http_app import ApiKeyGuard
from odin_mcp.zoho_tools import register_zoho_tools

API_KEY = "mounted-test-key"
ROOT = Path(__file__).resolve().parent.parent

COMBOS = [
    ("none", {}, set()),
    ("books-id-only", dict(ENV_BOOKS), set()),
    ("om-only", dict(ENV_OM), CRM_ALL),
    ("om-with-books", {**ENV_OM, **ENV_BOOKS}, CRM_ALL | BOOKS_ALL),
    ("ado-only", dict(ENV_ADO), CRM_ALL),
    ("ado-with-books-id", {**ENV_ADO, **ENV_BOOKS}, CRM_ALL),
    ("both-no-books-id", {**ENV_OM, **ENV_ADO}, CRM_ALL),
    ("both", dict(ENV_BOTH), CRM_ALL | BOOKS_ALL),
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
    async with mounted(ENV_BOTH) as (_, _, app), httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://localhost") as raw:
        body = {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}
        headers = {"Accept": "application/json, text/event-stream"}
        assert (await raw.post("/", json=body, headers=headers)).status_code == 401
        bad = {**headers, "Authorization": "Bearer wrong"}
        assert (await raw.post("/", json=body, headers=bad)).status_code == 401


async def test_tool_schemas_expose_enums_and_no_books_organization(router):
    async with mounted(ENV_BOTH) as (session, _, _):
        await session.open()
        tools = {t["name"]: t["inputSchema"]["properties"]
                 for t in (await session.rpc("tools/list"))["result"]["tools"]}
    assert all("organization_id" not in props for props in tools.values())
    search = tools["zoho_crm_search"]
    assert set(search["org"]["enum"]) == {"om", "ado"}
    assert set(search["module"]["enum"]) == {"Leads", "Contacts", "Deals", "Accounts"}
    assert {"word", "email", "phone", "full_name"} <= set(search["by"]["enum"])
    writers = ("zoho_crm_create", "zoho_crm_update", "zoho_books_create", "zoho_books_update")
    assert not [n for n, props in tools.items()
                if "fields" in props and not n.startswith(writers)]
    assert "enum" in tools["zoho_books_list_estimates"]["status"]["anyOf"][0]


async def test_registration_logs_under_the_mcp_zoho_logger(caplog):
    caplog.set_level(logging.INFO)
    _build(dict(ENV_OM))
    assert any(r.name == "odin.mcp.zoho" for r in caplog.records)


async def test_crm_search_returns_data_from_mocked_zoho(router):
    TokenMock(router)
    route = router.get(f"{API}/crm/v6/Leads/search", params={
        "criteria": "(Full_Name:equals:Erika Muster)", "page": "1", "per_page": "50"}).mock(
        return_value=httpx.Response(200, json={
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
    org = {"organization_id": BOOKS_ORG_ID}
    paging = {"page": "1", "per_page": "50"}
    notes_fields = "Note_Title,Note_Content,Created_Time,Modified_Time,Owner"
    for path, params, body in [
        ("/crm/v6/Contacts/5", {"fields": crm.DEFAULT_FIELDS["Contacts"]}, {"data": [{"id": "5"}]}),
        ("/crm/v6/Contacts", {"fields": crm.DEFAULT_FIELDS["Contacts"], **paging},
         {"data": [{"id": "6"}], "info": {"page": 1}}),
        ("/crm/v6/Contacts/5/Notes", {"fields": notes_fields, **paging},
         {"data": [{"id": "n"}], "info": {"page": 1}}),
        ("/books/v3/contacts", {"search_text": "x", **paging, **org},
         {"contacts": [{"contact_id": "c"}]}),
        ("/books/v3/contacts/8", org, {"contact": {"contact_id": "8"}}),
        ("/books/v3/estimates", {"status": "sent", **paging, **org},
         {"estimates": [{"estimate_id": "e"}]}),
        ("/books/v3/estimates/9", org, {"estimate": {"estimate_id": "9"}}),
    ]:
        router.get(f"{API}{path}", params=params).mock(
            return_value=httpx.Response(200, json=body))
    calls = [
        ("zoho_crm_get", {"org": "ado", "module": "Contacts", "id": "5"}),
        ("zoho_crm_list", {"org": "ado", "module": "Contacts"}),
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
    route = router.get(f"{API}/books/v3/contacts/1", params={
        "organization_id": BOOKS_ORG_ID}).mock(
        return_value=httpx.Response(200, json={"contact": {"contact_id": "1"}}))
    async with mounted(ENV_BOTH) as (session, _, _):
        await session.open()
        result = await session.call("zoho_books_get_contact",
                                    {"id": "1", "organization_id": "999"})
    assert route.call_count == 1
    assert route.calls.last.request.url.params["organization_id"] == BOOKS_ORG_ID
    assert "999" not in str(route.calls.last.request.url)
    assert tool_json(result) == {"data": {"contact_id": "1"}}


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


SECRET_BODIES = [
    {"code": ENV_OM["ZOHO_OM_CLIENT_SECRET"]},
    {"error": ENV_OM["ZOHO_OM_REFRESH_TOKEN"]},
    {"code": "INVALID_DATA", "message": ENV_OM["ZOHO_OM_CLIENT_SECRET"]},
    {"code": ENV_OM["ZOHO_OM_REFRESH_TOKEN"], "message": ENV_OM["ZOHO_OM_CLIENT_SECRET"],
     "error": ENV_OM["ZOHO_OM_REFRESH_TOKEN"]},
]
OWN_SECRETS = (ENV_OM["ZOHO_OM_CLIENT_SECRET"], ENV_OM["ZOHO_OM_REFRESH_TOKEN"], "AT-om-")


@pytest.mark.parametrize("body", SECRET_BODIES)
async def test_upstream_data_error_never_reaches_tool_result_or_logs(body, router, caplog):
    caplog.set_level(logging.DEBUG)
    TokenMock(router)
    router.get(f"{API}/crm/v6/Leads/1", params={"fields": crm.DEFAULT_FIELDS["Leads"]}).mock(
        return_value=httpx.Response(400, json=body))
    async with mounted(ENV_BOTH) as (session, _, _):
        await session.open()
        result = await session.call("zoho_crm_get", {"org": "om", "module": "Leads", "id": "1"})
    assert result["isError"] is True
    text = result["content"][0]["text"] + caplog.text
    assert not any(secret in text for secret in OWN_SECRETS)


@pytest.mark.parametrize("body", SECRET_BODIES)
async def test_upstream_token_error_never_reaches_tool_result_or_logs(body, router, caplog):
    caplog.set_level(logging.DEBUG)
    router.post(TOKEN_URL).mock(return_value=httpx.Response(400, json=body))
    async with mounted(ENV_BOTH) as (session, _, _):
        await session.open()
        result = await session.call("zoho_crm_get", {"org": "om", "module": "Leads", "id": "1"})
    assert result["isError"] is True
    text = result["content"][0]["text"] + caplog.text
    assert not any(secret in text for secret in OWN_SECRETS)


async def test_crm_get_returns_only_allowlisted_fields_by_default(router):
    TokenMock(router)
    route = router.get(f"{API}/crm/v6/Contacts/5", params={
        "fields": crm.DEFAULT_FIELDS["Contacts"]}).mock(
        return_value=httpx.Response(200, json={"data": [{"id": "5", "Full_Name": "A"}]}))
    async with mounted(ENV_BOTH) as (session, _, _):
        await session.open()
        result = await session.call("zoho_crm_get", {"org": "om", "module": "Contacts",
                                                     "id": "5"})
    assert route.call_count == 1
    assert tool_json(result) == {"data": [{"id": "5", "Full_Name": "A"}]}


UPSTREAM_RECORD = {
    "id": "5", "Full_Name": "Max Muster", "Email": "max@example.com",
    "Description": "SECRET-DESCRIPTION", "Custom_Field__c": "SECRET-CUSTOM",
    "Created_By": {"name": "Eve", "id": "77", "email": "eve@example.com"},
    "Owner": {"name": "David", "id": "99", "email": "owner@example.com"},
    "Account_Name": {"name": "Acme", "id": "55"}, "$approval": {"delegate": False},
    "Tag": [{"name": "SECRET-TAG"}], "Mailing_Street": "SECRET-STREET",
}
FORBIDDEN_TEXT = ("SECRET", "eve@example.com", "owner@example.com", "$approval", '"77"', '"99"')
UPSTREAM_NOTE = {
    "id": "n1", "Note_Title": "T", "Note_Content": "C", "Created_Time": "c",
    "Modified_Time": "m", "Owner": {"name": "David", "id": "99", "email": "owner@example.com"},
    "Created_By": {"name": "Eve", "email": "eve@example.com"}, "$editable": True,
    "Parent_Id": {"id": "SECRET-PARENT"},
}


async def _call_all_crm_tools(router) -> dict[str, str]:
    TokenMock(router)
    paging = {"page": "1", "per_page": "50"}
    fields = crm.DEFAULT_FIELDS["Contacts"]
    body = {"data": [UPSTREAM_RECORD], "info": {"page": 1}}
    router.get(f"{API}/crm/v6/Contacts/5", params={"fields": fields}).mock(
        return_value=httpx.Response(200, json=body))
    router.get(f"{API}/crm/v6/Contacts", params={"fields": fields, **paging}).mock(
        return_value=httpx.Response(200, json=body))
    router.get(f"{API}/crm/v6/Contacts/search", params={"word": "max", **paging}).mock(
        return_value=httpx.Response(200, json=body))
    router.get(f"{API}/crm/v6/Contacts/5/Notes", params={
        "fields": "Note_Title,Note_Content,Created_Time,Modified_Time,Owner", **paging}).mock(
        return_value=httpx.Response(200, json={"data": [UPSTREAM_NOTE], "info": {"page": 1}}))
    base = {"org": "om", "module": "Contacts"}
    out = {}
    async with mounted(ENV_BOTH) as (session, _, _):
        await session.open()
        for name, args in [("zoho_crm_get", {**base, "id": "5"}), ("zoho_crm_list", base),
                           ("zoho_crm_search", {**base, "by": "word", "value": "max"}),
                           ("zoho_crm_list_notes", {**base, "id": "5"})]:
            result = await session.call(name, args)
            assert not result.get("isError"), (name, result)
            out[name] = result["content"][0]["text"]
    return out


async def test_crm_tool_results_never_contain_forbidden_upstream_fields(router):
    texts = await _call_all_crm_tools(router)
    for name, text in texts.items():
        assert not any(bad in text for bad in FORBIDDEN_TEXT), (name, text)
    for name in ("zoho_crm_get", "zoho_crm_list", "zoho_crm_search"):
        record = tool_json({"content": [{"text": texts[name]}]})["data"][0]
        assert record == {"id": "5", "Full_Name": "Max Muster", "Email": "max@example.com",
                          "Owner": {"name": "David"}, "Account_Name": {"name": "Acme"}}
    note = tool_json({"content": [{"text": texts["zoho_crm_list_notes"]}]})["data"][0]
    assert note == {"id": "n1", "Note_Title": "T", "Note_Content": "C", "Created_Time": "c",
                    "Modified_Time": "m", "Owner": {"name": "David"}}


@pytest.mark.parametrize("by", ["email", "bogus"])
async def test_literal_types_reject_unknown_values_before_zoho(by, router):
    token = TokenMock(router)
    async with mounted(ENV_BOTH) as (session, _, _):
        await session.open()
        args = {"org": "om", "module": "Leads", "by": by, "value": "xy"}
        args["org" if by == "bogus" else "module"] = "nope"
        result = await session.call("zoho_crm_search", args)
    assert result["isError"] is True
    assert token.calls == 0 and not data_calls(router)


def test_invalid_config_registers_nothing_and_does_not_raise(caplog):
    caplog.set_level(logging.WARNING)
    mcp = FastMCP("zoho-test")
    bad = {**ENV_OM, "ZOHO_API_DOMAIN": "http://evil.example"}
    assert register_zoho_tools(mcp, env=bad) == []
    assert any(r.levelno == logging.WARNING for r in caplog.records)
    assert "evil.example" not in caplog.text


def test_real_server_registers_no_zoho_tools_without_credentials():
    """Fresh interpreter with a clean env: independent of import order in this test run."""
    code = ("import asyncio, json, odin_mcp.server as s; "
            "print(json.dumps(sorted(t.name for t in asyncio.run(s.mcp.list_tools()))))")
    env = {k: v for k, v in os.environ.items() if not k.startswith("ZOHO_")}
    env.update(PYTHONPATH=str(ROOT), TELEGRAM_BOT_TOKEN="dummy", AZURE_OPENAI_API_KEY="dummy",
               AZURE_OPENAI_ENDPOINT="https://dummy.example")
    proc = subprocess.run([sys.executable, "-c", code], cwd=ROOT, env=env, check=True,
                          capture_output=True, text=True, timeout=120)
    names = json.loads(proc.stdout.strip().splitlines()[-1])
    assert not [n for n in names if n.startswith("zoho_")]
    assert "search_knowledge" in names
