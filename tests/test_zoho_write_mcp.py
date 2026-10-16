"""Write tools behind the mounted MCP app: schemas, docstrings, calls, rejections."""
import json

import httpx
import pytest
import respx
from test_zoho_mcp_mounted import mounted
from zoho_support import (
    API,
    BOOKS_ORG_ID,
    BOOKS_WRITE_TOOLS,
    CRM_WRITE_TOOLS,
    ENV_BOTH,
    ENV_OM,
    TokenMock,
    data_calls,
    tool_json,
)

ORG = {"organization_id": BOOKS_ORG_ID}
CRM_OK = httpx.Response(201, json={"data": [{"code": "SUCCESS", "status": "success",
                                             "details": {"id": "1"}}]})


@pytest.fixture
def router():
    with respx.mock(assert_all_called=False) as r:
        yield r


async def _tools(env):
    async with mounted(env) as (session, _, _):
        await session.open()
        return {t["name"]: t for t in (await session.rpc("tools/list"))["result"]["tools"]}


async def test_docstrings_state_that_no_mail_is_ever_sent():
    tools = await _tools(ENV_BOTH)
    for name in CRM_WRITE_TOOLS | BOOKS_WRITE_TOOLS:
        assert "sendet nie Mails" in tools[name]["description"], name


async def test_schemas_have_no_trigger_owner_or_reminder_parameters():
    tools = await _tools(ENV_BOTH)
    forbidden = {"trigger", "lar_id", "owner", "remind_at", "reminder", "send", "organization_id",
                 "skip_feature_execution", "process", "to_mail_ids", "send_email"}
    for name in CRM_WRITE_TOOLS | BOOKS_WRITE_TOOLS:
        props = {p.lower() for p in tools[name]["inputSchema"]["properties"]}
        assert not props & forbidden, name
    task = tools["zoho_crm_create_task"]["inputSchema"]["properties"]
    assert set(task) == {"org", "subject", "due_date", "related_module", "related_id",
                         "description"}
    assert set(tools["zoho_books_mark_estimate"]["inputSchema"]["properties"]["status"]
               ["enum"]) == {"sent", "declined"}


async def test_no_delete_tool_exists():
    tools = await _tools(ENV_BOTH)
    assert not [n for n in tools if "delete" in n or "invoice" in n or "payment" in n
                or "send" in n or "mail" in n]


async def test_om_only_without_books_id_has_crm_write_but_no_books_write():
    tools = await _tools(ENV_OM)
    assert CRM_WRITE_TOOLS <= set(tools) and not BOOKS_WRITE_TOOLS & set(tools)


async def test_crm_write_tools_end_to_end(router):
    TokenMock(router)
    create = router.post(f"{API}/crm/v6/Leads").mock(return_value=CRM_OK)
    update = router.put(f"{API}/crm/v6/Leads/1").mock(return_value=CRM_OK)
    note = router.post(f"{API}/crm/v6/Leads/1/Notes").mock(return_value=CRM_OK)
    task = router.post(f"{API}/crm/v6/Tasks").mock(return_value=CRM_OK)
    calls = [
        ("zoho_crm_create", {"org": "ado", "module": "Leads", "fields": {"Last_Name": "M"}}),
        ("zoho_crm_update", {"org": "om", "module": "Leads", "id": "1",
                             "fields": {"Company": "X"}}),
        ("zoho_crm_add_note", {"org": "om", "module": "Leads", "id": "1", "title": "t",
                               "content": "c"}),
        ("zoho_crm_create_task", {"org": "om", "subject": "s", "due_date": "2026-11-02",
                                  "related_module": "Contacts", "related_id": "5"}),
    ]
    async with mounted(ENV_BOTH) as (session, _, _):
        await session.open()
        for name, args in calls:
            payload = tool_json(await session.call(name, args))
            assert payload == {"ok": True, "results": [
                {"id": "1", "status": "success", "code": "SUCCESS"}]}, name
    assert [r.call_count for r in (create, update, note, task)] == [1, 1, 1, 1]
    assert json.loads(task.calls.last.request.content)["data"][0]["Who_Id"] == {"id": "5"}


async def test_books_write_tools_end_to_end(router):
    TokenMock(router)
    contact = router.post(f"{API}/books/v3/contacts", params=ORG).mock(
        return_value=httpx.Response(201, json={"code": 0, "contact": {"contact_id": "1"}}))
    upd_contact = router.put(f"{API}/books/v3/contacts/1", params=ORG).mock(
        return_value=httpx.Response(200, json={"code": 0, "contact": {"contact_id": "1"}}))
    est = router.post(f"{API}/books/v3/estimates", params=ORG).mock(
        return_value=httpx.Response(201, json={"code": 0, "estimate": {"estimate_id": "2"}}))
    upd_est = router.put(f"{API}/books/v3/estimates/2", params=ORG).mock(
        return_value=httpx.Response(200, json={"code": 0, "estimate": {"estimate_id": "2"}}))
    mark = router.post(f"{API}/books/v3/estimates/2/status/sent", params=ORG).mock(
        return_value=httpx.Response(200, json={"code": 0}))
    item = router.post(f"{API}/books/v3/items", params=ORG).mock(
        return_value=httpx.Response(201, json={"code": 0, "item": {"item_id": "3"}}))
    items = router.get(f"{API}/books/v3/items", params={"page": "1", "per_page": "50", **ORG}
                       ).mock(return_value=httpx.Response(200, json={"items": []}))
    taxes = router.get(f"{API}/books/v3/settings/taxes", params=ORG).mock(
        return_value=httpx.Response(200, json={"taxes": []}))
    calls = [
        ("zoho_books_create_contact", {"fields": {"contact_name": "X"}}),
        ("zoho_books_update_contact", {"id": "1", "fields": {"contact_name": "Y"}}),
        ("zoho_books_create_estimate", {"fields": {"customer_id": "1"}}),
        ("zoho_books_update_estimate", {"id": "2", "fields": {"notes": "n"}}),
        ("zoho_books_mark_estimate", {"id": "2", "status": "sent"}),
        ("zoho_books_create_item", {"fields": {"name": "n"}}),
        ("zoho_books_list_items", {}),
        ("zoho_books_list_taxes", {}),
    ]
    async with mounted(ENV_BOTH) as (session, _, _):
        await session.open()
        for name, args in calls:
            result = await session.call(name, args)
            assert not result.get("isError"), (name, result)
    assert all(r.call_count == 1 for r in
               (contact, upd_contact, est, upd_est, mark, item, items, taxes))


@pytest.mark.parametrize("name,args", [
    ("zoho_crm_create", {"org": "om", "module": "Leads", "fields": {"trigger": []}}),
    ("zoho_crm_create", {"org": "om", "module": "Leads", "fields": {"Owner": {"id": "1"}}}),
    ("zoho_crm_create", {"org": "om", "module": "Users", "fields": {"A": "x"}}),
    ("zoho_crm_update", {"org": "om", "module": "Leads", "id": "1", "fields": {"Remind_At": "x"}}),
    ("zoho_crm_create_task", {"org": "om", "subject": "s", "due_date": "morgen"}),
    ("zoho_books_create_estimate", {"fields": {"customer_id": "1", "to_mail_ids": ["a@b.de"]}}),
    ("zoho_books_create_contact", {"fields": {"contact_name": "x", "is_portal_enabled": True}}),
    ("zoho_books_mark_estimate", {"id": "1", "status": "draft"}),
    ("zoho_books_mark_estimate", {"id": "1", "status": "accepted"}),
    ("zoho_books_update_estimate", {"id": "1/email", "fields": {"notes": "x"}}),
])
async def test_rejected_write_is_tool_error_with_zero_upstream_calls(name, args, router):
    token = TokenMock(router)
    async with mounted(ENV_BOTH) as (session, _, _):
        await session.open()
        result = await session.call(name, args)
    assert result["isError"] is True
    assert token.calls == 0 and not data_calls(router)


async def test_write_tool_logs_no_values(router, caplog):
    import logging
    caplog.set_level(logging.DEBUG)
    TokenMock(router)
    router.post(f"{API}/crm/v6/Leads").mock(return_value=CRM_OK)
    async with mounted(ENV_BOTH) as (session, _, _):
        await session.open()
        await session.call("zoho_crm_create", {
            "org": "om", "module": "Leads", "fields": {"Last_Name": "Geheimname-123"}})
    assert "Geheimname-123" not in caplog.text
