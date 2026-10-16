"""CRM responses are projected to the fixed per-module allowlist (nothing else leaves)."""
import httpx
import pytest
import respx
from zoho_support import API, ENV_BOTH, TokenMock

from integrations.zoho import crm
from integrations.zoho.client import ZohoClient, ZohoConfig
from integrations.zoho.validation import NOTES_FIELDS


@pytest.fixture
def router():
    with respx.mock(assert_all_called=False) as r:
        yield r


@pytest.fixture
def client(router):
    TokenMock(router)
    return ZohoClient(ZohoConfig.from_env(ENV_BOTH))


def _ok(router, path, params, body):
    str_params = {k: str(v) for k, v in params.items()}
    return router.get(f"{API}{path}", params=str_params).mock(
        return_value=httpx.Response(200, json=body))


def _paging(page=1):
    return {"page": page, "per_page": 50}


async def test_callers_cannot_choose_fields():
    for fn in (crm.get_record, crm.list_records, crm.search, crm.list_notes):
        with pytest.raises(TypeError):
            await fn(None, "om", "Leads", "1", "x", fields=["Description"])


FORBIDDEN_RECORD = {
    "id": "1", "Full_Name": "A", "Email": "a@b.de", "Description": "private note text",
    "Created_By": {"name": "Eve", "id": "7", "email": "eve@example.com"},
    "Owner": {"name": "David", "id": "9", "email": "owner@example.com"},
    "Account_Name": {"name": "Acme", "id": "5"}, "Contact_Name": {"name": "Max", "id": "6"},
    "Custom_Secret__c": "custom", "$approval": {"x": 1}, "$editable": True,
    "Tag": [{"name": "t"}], "Deal_Name": "D", "Stage": "Won",
}
LEAK = ("private note text", "eve@example.com", "owner@example.com", "custom", "'id': '9'",
        "'id': '5'", "'id': '7'")


def _assert_projected(data: list[dict], module: str):
    allowed = set(crm.DEFAULT_FIELDS[module].split(",")) | {"id"}
    for record in data:
        assert set(record) <= allowed, set(record) - allowed
        for value in record.values():
            assert not isinstance(value, (list, dict)) or set(value) == {"name"}
    assert not any(leak in repr(data) for leak in LEAK)


async def test_get_projects_every_record_to_the_module_allowlist(client, router):
    _ok(router, "/crm/v6/Deals/1", {"fields": crm.DEFAULT_FIELDS["Deals"]},
        {"data": [FORBIDDEN_RECORD]})
    out = await crm.get_record(client, "om", "Deals", "1")
    _assert_projected(out["data"], "Deals")
    assert out["data"][0]["Account_Name"] == {"name": "Acme"}
    assert out["data"][0]["Owner"] == {"name": "David"}


async def test_list_projects_every_record_to_the_module_allowlist(client, router):
    _ok(router, "/crm/v6/Deals", {"fields": crm.DEFAULT_FIELDS["Deals"], **_paging()},
        {"data": [FORBIDDEN_RECORD, FORBIDDEN_RECORD]})
    out = await crm.list_records(client, "om", "Deals")
    assert len(out["data"]) == 2
    _assert_projected(out["data"], "Deals")


async def test_search_projects_every_record_to_the_module_allowlist(client, router):
    _ok(router, "/crm/v6/Deals/search", {"word": "ab", **_paging()},
        {"data": [FORBIDDEN_RECORD]})
    out = await crm.search(client, "om", "Deals", "word", "ab")
    _assert_projected(out["data"], "Deals")


async def test_notes_are_projected_to_title_content_times_and_owner_name(client, router):
    note = {"id": "n1", "Note_Title": "T", "Note_Content": "C", "Created_Time": "c",
            "Modified_Time": "m", "Owner": {"name": "D", "id": "1", "email": "d@example.com"},
            "Created_By": {"name": "E", "email": "e@example.com"}, "Parent_Id": {"id": "5"},
            "$editable": True, "Voice_Note": True, "Note_Owner": "x"}
    _ok(router, "/crm/v6/Leads/5/Notes", {"fields": NOTES_FIELDS, **_paging()},
        {"data": [note]})
    out = await crm.list_notes(client, "om", "Leads", "5")
    assert out["data"] == [{"id": "n1", "Note_Title": "T", "Note_Content": "C",
                            "Created_Time": "c", "Modified_Time": "m", "Owner": {"name": "D"}}]


async def test_default_fields_are_minimal_per_module():
    assert crm.DEFAULT_FIELDS["Deals"] == (
        "Deal_Name,Stage,Amount,Closing_Date,Account_Name,Contact_Name,Owner")
    sensitive = {"Description", "Date_of_Birth", "Mailing_Street", "Street", "Skype_ID",
                 "Secondary_Email", "Twitter", "Billing_Street", "Home_Phone"}
    for module in crm.MODULES:
        fields = set(crm.DEFAULT_FIELDS[module].split(","))
        assert not fields & sensitive, module
        assert "Owner" in fields and len(fields) <= 20
    for module in ("Leads", "Contacts"):
        fields = set(crm.DEFAULT_FIELDS[module].split(","))
        assert {"Full_Name", "Email", "Phone", "Mobile", "Created_Time", "Modified_Time"} <= fields


async def test_list_records_uses_the_fixed_fields_and_returns_paging(client, router):
    body = {"data": [{"id": "1"}], "info": {"page": 2, "more_records": True, "per_page": 50}}
    route = _ok(router, "/crm/v6/Deals", {"fields": crm.DEFAULT_FIELDS["Deals"], **_paging(2)},
                body)
    out = await crm.list_records(client, "om", "Deals", page=2)
    assert dict(route.calls.last.request.url.params) == {
        "fields": crm.DEFAULT_FIELDS["Deals"], "page": "2", "per_page": "50"}
    assert out == {"data": [{"id": "1"}], "page": 2, "more_records": True}

