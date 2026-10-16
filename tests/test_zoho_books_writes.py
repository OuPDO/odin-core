"""Books reads (items, taxes) and write operations against mocked Zoho."""
import json

import httpx
import pytest
import respx
from zoho_support import (
    API,
    BOOKS_ORG_ID,
    ENV_ADO,
    ENV_BOTH,
    ENV_OM,
    TokenMock,
    data_calls,
)

from integrations.zoho import books, books_write
from integrations.zoho.client import (
    ZohoAPIError,
    ZohoClient,
    ZohoConfig,
    ZohoOutcomeUnknown,
    ZohoRequestError,
)

ORG = {"organization_id": BOOKS_ORG_ID}


@pytest.fixture
def router():
    with respx.mock(assert_all_called=False) as r:
        yield r


@pytest.fixture
def client():
    return ZohoClient(ZohoConfig.from_env(ENV_BOTH))


def sent(route) -> dict:
    return json.loads(route.calls.last.request.content)


# ---- reads -----------------------------------------------------------------------------------

async def test_list_items_projects_fields(client, router):
    TokenMock(router)
    route = router.get(f"{API}/books/v3/items", params={
        "page": "2", "per_page": "50", **ORG}).mock(return_value=httpx.Response(200, json={
            "items": [{"item_id": "1", "name": "Beratung", "rate": 120, "description": "h",
                       "unit": "Std", "tax_id": "9", "purchase_account_id": "SECRET",
                       "stock_on_hand": 4}],
            "page_context": {"page": 2, "has_more_page": True}}))
    result = await books.list_items(client, 2)
    assert route.call_count == 1
    assert result == {"data": [{"item_id": "1", "name": "Beratung", "rate": 120,
                                "description": "h", "unit": "Std", "tax_id": "9"}],
                      "page": 2, "has_more_page": True}


async def test_list_taxes_projects_fields(client, router):
    TokenMock(router)
    route = router.get(f"{API}/books/v3/settings/taxes", params=ORG).mock(
        return_value=httpx.Response(200, json={"taxes": [
            {"tax_id": "3", "tax_name": "USt 19", "tax_percentage": 19, "tax_type": "tax",
             "country_code": "DE"}]}))
    result = await books.list_taxes(client)
    assert route.call_count == 1
    assert result == {"data": [{"tax_id": "3", "tax_name": "USt 19", "tax_percentage": 19}]}


@pytest.mark.parametrize("page", [0, 101, True, "1", None])
async def test_list_items_page_validated(page, client, router):
    token = TokenMock(router)
    with pytest.raises(ZohoRequestError):
        await books.list_items(client, page)
    assert token.calls == 0 and not data_calls(router)


# ---- writes: happy paths ---------------------------------------------------------------------

CONTACT = {"contact_name": "Muster GmbH", "company_name": "Muster GmbH",
           "contact_persons": [{"first_name": "Erika", "last_name": "Muster",
                                "email": "e@example.org", "is_primary_contact": True}]}
ESTIMATE = {"customer_id": "1000", "date": "2026-10-16", "line_items": [
    {"item_id": "5", "quantity": 2, "rate": 90.5, "tax_id": "3", "description": "Workshop"}]}


async def test_create_contact(client, router):
    TokenMock(router)
    route = router.post(f"{API}/books/v3/contacts", params=ORG, json=CONTACT).mock(
        return_value=httpx.Response(201, json={"code": 0, "message": "SECRET", "contact": {
            "contact_id": "42", "contact_name": "Muster GmbH", "status": "active",
            "billing_address": {"street": "SECRET"}}}))
    result = await books_write.create_contact(client, CONTACT)
    assert route.call_count == 1
    assert result == {"ok": True, "code": 0, "data": {
        "contact_id": "42", "contact_name": "Muster GmbH", "status": "active"}}


async def test_update_contact(client, router):
    TokenMock(router)
    route = router.put(f"{API}/books/v3/contacts/42", params=ORG,
                       json={"contact_name": "Neu"}).mock(
        return_value=httpx.Response(200, json={"code": 0, "contact": {"contact_id": "42"}}))
    result = await books_write.update_contact(client, "42", {"contact_name": "Neu"})
    assert route.call_count == 1 and result["data"] == {"contact_id": "42"}


async def test_create_and_update_estimate(client, router):
    TokenMock(router)
    create = router.post(f"{API}/books/v3/estimates", params=ORG, json=ESTIMATE).mock(
        return_value=httpx.Response(201, json={"code": 0, "estimate": {
            "estimate_id": "7", "estimate_number": "EST-1", "status": "draft", "total": 181,
            "customer_id": "1000", "line_items": [{"name": "SECRET"}]}}))
    update = router.put(f"{API}/books/v3/estimates/7", params=ORG,
                        json={"notes": "Danke"}).mock(
        return_value=httpx.Response(200, json={"code": 0, "estimate": {"estimate_id": "7"}}))
    made = await books_write.create_estimate(client, ESTIMATE)
    changed = await books_write.update_estimate(client, "7", {"notes": "Danke"})
    assert create.call_count == update.call_count == 1
    assert made["data"] == {"estimate_id": "7", "estimate_number": "EST-1", "status": "draft",
                            "total": 181, "customer_id": "1000"}
    assert changed["data"] == {"estimate_id": "7"}
    assert "send" not in create.calls.last.request.url.params


@pytest.mark.parametrize("state", ["sent", "declined"])
async def test_mark_estimate_has_no_body_and_no_query_beyond_org(state, client, router):
    TokenMock(router)
    route = router.post(f"{API}/books/v3/estimates/7/status/{state}", params=ORG).mock(
        return_value=httpx.Response(200, json={"code": 0, "message": "SECRET"}))
    result = await books_write.mark_estimate(client, "7", state)
    assert route.call_count == 1
    assert route.calls.last.request.content == b""
    assert dict(route.calls.last.request.url.params) == ORG
    assert result == {"ok": True, "code": 0, "data": None}


async def test_create_item(client, router):
    TokenMock(router)
    route = router.post(f"{API}/books/v3/items", params=ORG,
                        json={"name": "Beratung", "rate": 120, "unit": "Std"}).mock(
        return_value=httpx.Response(201, json={"code": 0, "item": {
            "item_id": "11", "name": "Beratung", "rate": 120, "status": "active"}}))
    result = await books_write.create_item(client, {"name": "Beratung", "rate": 120,
                                                    "unit": "Std"})
    assert route.call_count == 1
    assert result["data"] == {"item_id": "11", "name": "Beratung", "rate": 120,
                              "status": "active"}


async def test_books_400_envelope_is_returned_sanitized_without_retry(client, router):
    TokenMock(router)
    route = router.post(f"{API}/books/v3/items").mock(return_value=httpx.Response(
        400, json={"code": 1001, "message": "SECRET-MESSAGE"}))
    result = await books_write.create_item(client, {"name": "x"})
    assert route.call_count == 1
    assert result == {"ok": False, "code": 1001, "data": None,
                      "message": "Zoho Books hat die Anfrage abgelehnt (Code 1001)"}
    assert "SECRET" not in str(result)


async def test_books_other_4xx_is_a_confirmed_rejection(client, router):
    TokenMock(router)
    router.post(f"{API}/books/v3/items").mock(return_value=httpx.Response(
        404, json={"code": 2, "message": "SECRET"}))
    with pytest.raises(ZohoAPIError, match="HTTP 404, 2") as exc:
        await books_write.create_item(client, {"name": "x"})
    assert not isinstance(exc.value, ZohoOutcomeUnknown)


@pytest.mark.parametrize("response", [
    httpx.Response(500, json={"code": 0}), httpx.Response(502, text="bad gateway"),
    httpx.Response(201, text="not json"), httpx.Response(201, json=[1]),
    httpx.Response(204), httpx.Response(201, json={"code": 0}),  # create without entity
    httpx.Response(201, json={"message": "no code"}),
    httpx.Response(201, json={"code": 0, "item": "x"}),
    httpx.Response(503),
])
async def test_books_unclear_result_is_outcome_unknown(response, client, router):
    TokenMock(router)
    route = router.post(f"{API}/books/v3/items").mock(return_value=response)
    with pytest.raises(ZohoOutcomeUnknown, match="outcome_unknown.*Ergebnis unbekannt"):
        await books_write.create_item(client, {"name": "x"})
    assert route.call_count == 1


async def test_books_nonzero_code_with_200_is_not_ok(client, router):
    TokenMock(router)
    router.post(f"{API}/books/v3/items").mock(return_value=httpx.Response(
        201, json={"code": 5, "item": {"item_id": "1"}}))
    assert (await books_write.create_item(client, {"name": "x"}))["ok"] is False


# ---- mail / portal attempts: zero network ----------------------------------------------------

@pytest.mark.parametrize("call", [
    lambda c: books_write.create_estimate(c, {**ESTIMATE, "to_mail_ids": ["a@example.org"]}),
    lambda c: books_write.create_estimate(c, {**ESTIMATE, "cc_mail_ids": ["a@example.org"]}),
    lambda c: books_write.create_estimate(c, {**ESTIMATE, "send": True}),
    lambda c: books_write.create_estimate(c, {**ESTIMATE, "email": "a@example.org"}),
    lambda c: books_write.update_estimate(c, "1", {"send_email": True}),
    lambda c: books_write.update_estimate(c, "1", {"mail_to": "x"}),
    lambda c: books_write.create_contact(c, {"contact_name": "X", "is_portal_enabled": True}),
    lambda c: books_write.create_contact(c, {"contact_name": "X", "contact_persons": [
        {"first_name": "A", "enable_portal": True}]}),
    lambda c: books_write.update_contact(c, "1", {"contact_persons": [
        {"is_portal_enabled": True}]}),
    lambda c: books_write.update_contact(c, "1", {"payment_reminder_enabled": True}),
    lambda c: books_write.create_item(c, {"name": "x", "send": True}),
    lambda c: books_write.mark_estimate(c, "1", "draft"),
    lambda c: books_write.mark_estimate(c, "1", "accepted"),
    lambda c: books_write.mark_estimate(c, "1", "send"),
    lambda c: books_write.mark_estimate(c, "1/email", "sent"),
    lambda c: books_write.mark_estimate(c, "abc", "sent"),
    lambda c: books_write.update_contact(c, "1/portal/enable", {"contact_name": "x"}),
    lambda c: books_write.update_estimate(c, "1/../2", {"notes": "x"}),
    lambda c: books_write.create_item(c, {}),
])
async def test_forbidden_or_malformed_writes_rejected_with_zero_network(call, client, router):
    token = TokenMock(router)
    with pytest.raises(ZohoRequestError):
        await call(client)
    assert token.calls == 0 and not data_calls(router)


@pytest.mark.parametrize("path,body", [
    ("/books/v3/estimates/1/email", {}),
    ("/books/v3/invoices/1/email", {}),
    ("/books/v3/contacts/1/portal/enable", {}),
])
async def test_mail_routes_unreachable_through_the_client(path, body, client, router):
    token = TokenMock(router)
    with pytest.raises(ZohoRequestError):
        await client.write("om", "POST", path, body)
    assert token.calls == 0 and not data_calls(router)


async def test_estimate_send_query_rejected_before_network(client, router):
    token = TokenMock(router)
    with pytest.raises(ZohoRequestError):
        await client.get("om", "/books/v3/estimates", {"send": "true", "page": 1,
                                                       "per_page": 50})
    assert token.calls == 0 and not data_calls(router)


async def test_books_writes_need_om_credentials_and_books_id(router):
    token = TokenMock(router)
    for env in (ENV_ADO, ENV_OM):  # ado only; om without ZOHO_BOOKS_ORGANIZATION_ID
        c = ZohoClient(ZohoConfig.from_env(env))
        with pytest.raises(ZohoRequestError):
            await books_write.create_item(c, {"name": "x"})
    assert token.calls == 0 and not data_calls(router)


async def test_books_write_not_retried_on_timeout_but_retried_on_invalid_token(client, router):
    token = TokenMock(router)
    route = router.post(f"{API}/books/v3/items").mock(side_effect=httpx.ReadTimeout("slow"))
    with pytest.raises(ZohoAPIError, match="outcome_unknown"):
        await books_write.create_item(client, {"name": "x"})
    assert route.call_count == 1
    route.mock(side_effect=[httpx.Response(401, json={"code": 57}),
                            httpx.Response(201, json={"code": 0, "item": {"item_id": "1"}})])
    result = await books_write.create_item(client, {"name": "y"})
    assert result["ok"] is True and route.call_count == 3 and token.calls == 2


@pytest.mark.parametrize("code", [True, "0", None, -1, 10**9])
async def test_books_unusable_code_is_outcome_unknown(code, client, router):
    TokenMock(router)
    router.post(f"{API}/books/v3/items").mock(return_value=httpx.Response(
        201, json={"code": code, "item": {"item_id": "1"}}))
    with pytest.raises(ZohoOutcomeUnknown):
        await books_write.create_item(client, {"name": "x"})


@pytest.mark.parametrize("entity,call", [
    ("item", lambda c: books_write.create_item(c, {"name": "x"})),  # review reproduction 3
    ("contact", lambda c: books_write.create_contact(c, {"contact_name": "x"})),
    ("estimate", lambda c: books_write.create_estimate(c, {"customer_id": "1"})),
])
@pytest.mark.parametrize("body", [{}, {"name": "x", "status": "active"}])
async def test_books_success_without_entity_id_is_outcome_unknown(entity, call, body, client,
                                                                  router):
    TokenMock(router)
    path = {"item": "items", "contact": "contacts", "estimate": "estimates"}[entity]
    route = router.post(f"{API}/books/v3/{path}").mock(return_value=httpx.Response(
        201, json={"code": 0, entity: body}))
    with pytest.raises(ZohoOutcomeUnknown):
        await call(client)
    assert route.call_count == 1
