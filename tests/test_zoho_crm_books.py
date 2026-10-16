"""Zoho CRM and Books request builders: exact requests, validation, minimisation."""
import httpx
import pytest
import respx
from zoho_support import API, BOOKS_ORG_ID, ENV_BOTH, TokenMock, data_calls

from integrations.zoho import books, crm
from integrations.zoho.client import (
    ZohoAPIError,
    ZohoClient,
    ZohoConfig,
    ZohoRequestError,
)

NOTES_FIELDS = "Note_Title,Note_Content,Created_Time,Modified_Time,Owner"


@pytest.fixture
def router():
    with respx.mock(assert_all_called=False) as r:
        yield r


@pytest.fixture
def client(router):
    TokenMock(router)
    return ZohoClient(ZohoConfig.from_env(ENV_BOTH))


def _ok(router, path, params, body=None, status=200):
    """Mock one endpoint; the mock only matches when the exact mandatory query is sent."""
    str_params = {k: str(v) for k, v in params.items()}
    return router.get(f"{API}{path}", params=str_params).mock(
        return_value=httpx.Response(status, json=body if body is not None else {"data": []}))


def _params(route):
    return dict(route.calls.last.request.url.params)


def _paging(page=1):
    return {"page": page, "per_page": 50}


# ---- CRM search ------------------------------------------------------------

@pytest.mark.parametrize("by", ["word", "email", "phone"])
async def test_search_by_query_key(client, router, by):
    params = {by: "müller@example.com", **_paging()}
    route = _ok(router, "/crm/v6/Leads/search", params, {
        "data": [{"id": "1", "$editable": True, "Full_Name": "A"}],
        "info": {"page": 1, "per_page": 50, "count": 1, "more_records": False}})
    out = await crm.search(client, "om", "Leads", by, "müller@example.com")
    assert _params(route) == {k: str(v) for k, v in params.items()}
    assert out == {"data": [{"id": "1", "Full_Name": "A"}], "page": 1, "more_records": False}


@pytest.mark.parametrize("module,by,field", [
    ("Leads", "full_name", "Full_Name"), ("Leads", "company", "Company"),
    ("Contacts", "full_name", "Full_Name"), ("Contacts", "account", "Account_Name"),
    ("Accounts", "name", "Account_Name"), ("Deals", "name", "Deal_Name"),
])
async def test_field_search_mapping(client, router, module, by, field):
    params = {"criteria": f"({field}:equals:Acme)", **_paging(3)}
    route = _ok(router, f"/crm/v6/{module}/search", params)
    await crm.search(client, "om", module, by, "Acme", page=3)
    assert _params(route) == {k: str(v) for k, v in params.items()}


@pytest.mark.parametrize("module,by", [
    ("Leads", "account"), ("Leads", "name"), ("Contacts", "company"), ("Contacts", "name"),
    ("Accounts", "full_name"), ("Accounts", "company"), ("Deals", "full_name"),
    ("Deals", "account"), ("Leads", "Full_Name"), ("Leads", "word&x"), ("Leads", ""),
    ("Leads", "criteria"), ("Leads", "page"),
])
async def test_other_search_combinations_rejected(client, router, module, by):
    with pytest.raises(ZohoRequestError):
        await crm.search(client, "om", module, by, "xy")
    assert not data_calls(router)


async def test_criteria_escaping(client, router):
    params = {"criteria": r"(Company:equals:A\(b\)\,c\\d)", **_paging()}
    route = _ok(router, "/crm/v6/Leads/search", params)
    await crm.search(client, "om", "Leads", "company", r"A(b),c\d")
    assert _params(route)["criteria"] == r"(Company:equals:A\(b\)\,c\\d)"


@pytest.mark.parametrize("value", ["x&send=true", "x)or(", "x)or(Email:equals:a", "a=b&page=9",
                                   "x#frag", "x y", "%26", "x;y", "üb"])
@pytest.mark.parametrize("by", ["word", "company"])
async def test_value_injection_stays_in_one_parameter(client, router, by, value):
    route = router.get(f"{API}/crm/v6/Leads/search").mock(
        return_value=httpx.Response(200, json={"data": []}))
    await crm.search(client, "om", "Leads", by, value)
    url = route.calls.last.request.url
    assert sorted(url.params.keys()) == sorted(["criteria" if by == "company" else by,
                                                "page", "per_page"])
    assert "send" not in url.params
    assert "&send=true" not in str(url)
    assert url.params["page"] == "1"


@pytest.mark.parametrize("value", ["", " ", None, 5, "a", " a ", "a" * 201, "a\x00b", "a\nb"])
async def test_bad_search_values_rejected(client, router, value):
    for by in ("word", "company"):
        with pytest.raises(ZohoRequestError):
            await crm.search(client, "om", "Leads", by, value)
    assert not data_calls(router)


async def test_search_value_length_edges(client, router):
    router.get(f"{API}/crm/v6/Leads/search").mock(return_value=httpx.Response(204))
    await crm.search(client, "om", "Leads", "word", "ab")
    await crm.search(client, "om", "Leads", "word", "a" * 200)
    await crm.search(client, "om", "Leads", "company", "a" * 200)


async def test_search_no_content_is_empty_result(client, router):
    router.get(f"{API}/crm/v6/Leads/search").mock(return_value=httpx.Response(204))
    out = await crm.search(client, "om", "Leads", "word", "nobody")
    assert out == {"data": [], "page": 1, "more_records": False}


async def test_search_output_is_reduced_to_the_allowlist(client, router):
    record = {"id": "1", "Full_Name": "A", "Email": "a@b.de", "Description": "private notes",
              "Date_of_Birth": "1980-01-01", "Mailing_Street": "Secret Str. 1",
              "Owner": {"name": "David", "id": "9", "email": "owner@example.com"}}
    _ok(router, "/crm/v6/Leads/search", {"word": "ab", **_paging()}, {"data": [record]})
    out = await crm.search(client, "om", "Leads", "word", "ab")
    assert out["data"] == [{"id": "1", "Full_Name": "A", "Email": "a@b.de",
                            "Owner": {"name": "David"}}]


# ---- CRM validation shared by all tools ------------------------------------

@pytest.mark.parametrize("module", ["Users", "leads", "Leads/1", "Leads?x", "", "Quotes", "../Leads"])
async def test_unknown_modules_rejected(client, router, module):
    with pytest.raises(ZohoRequestError):
        await crm.get_record(client, "om", module, "1")
    with pytest.raises(ZohoRequestError):
        await crm.list_records(client, "om", module)
    assert not data_calls(router)


@pytest.mark.parametrize("record_id", ["1/2", "..", "%2F", "1?x=1", "1&x=2", "1 2", "", "abc",
                                       "1" * 26, "١٢", "1\n", "-1", None, 12])
async def test_bad_ids_rejected_without_network(client, router, record_id):
    with pytest.raises(ZohoRequestError):
        await crm.get_record(client, "om", "Leads", record_id)
    with pytest.raises(ZohoRequestError):
        await crm.list_notes(client, "om", "Leads", record_id)
    assert not data_calls(router)


@pytest.mark.parametrize("page", [0, -1, 41, True, 1.5, "1", None])
async def test_crm_page_bounds(client, router, page):
    with pytest.raises(ZohoRequestError):
        await crm.search(client, "om", "Leads", "word", "xy", page=page)
    with pytest.raises(ZohoRequestError):
        await crm.list_records(client, "om", "Leads", page=page)
    with pytest.raises(ZohoRequestError):
        await crm.list_notes(client, "om", "Leads", "1", page=page)
    assert not data_calls(router)


@pytest.mark.parametrize("page", [1, 40])
async def test_crm_page_edges_pass(client, router, page):
    route = _ok(router, "/crm/v6/Leads",
                {"fields": crm.DEFAULT_FIELDS["Leads"], **_paging(page)})
    await crm.list_records(client, "om", "Leads", page=page)
    assert _params(route)["page"] == str(page)


# ---- CRM get / list / notes ------------------------------------------------

async def test_get_record_defaults_to_the_field_allowlist(client, router):
    route = _ok(router, "/crm/v6/Contacts/4711", {"fields": crm.DEFAULT_FIELDS["Contacts"]},
                {"data": [{"id": "4711", "$state": "save", "Full_Name": "X"}]})
    out = await crm.get_record(client, "ado", "Contacts", "4711")
    assert _params(route) == {"fields": crm.DEFAULT_FIELDS["Contacts"]}
    assert out == {"data": [{"id": "4711", "Full_Name": "X"}]}


async def test_list_notes_sends_the_fixed_field_list(client, router):
    route = _ok(router, "/crm/v6/Accounts/55/Notes", {"fields": NOTES_FIELDS, **_paging()}, {
        "data": [{"id": "n1", "Note_Content": "hi", "Owner": {"name": "D", "id": "1"}}],
        "info": {"page": 1, "more_records": False}})
    out = await crm.list_notes(client, "om", "Accounts", "55", page=1)
    assert _params(route) == {"fields": NOTES_FIELDS, "page": "1", "per_page": "50"}
    assert out["data"] == [{"id": "n1", "Note_Content": "hi", "Owner": {"name": "D"}}]
    assert out["more_records"] is False


async def test_upstream_error_becomes_clean_error(client, router):
    _ok(router, "/crm/v6/Leads/9", {"fields": crm.DEFAULT_FIELDS["Leads"]},
        {"code": "INVALID_DATA", "message": "nope"}, status=400)
    with pytest.raises(ZohoAPIError, match="400"):
        await crm.get_record(client, "om", "Leads", "9")


@pytest.mark.parametrize("body", [{"data": "x"}, {"data": {"id": "1"}}, {"data": [1, 2]},
                                  {"data": [{"id": "1"}], "info": "bad"}])
async def test_crm_unexpected_shapes_are_clean_errors(client, router, body):
    _ok(router, "/crm/v6/Leads", {"fields": crm.DEFAULT_FIELDS["Leads"], **_paging()}, body)
    with pytest.raises(ZohoAPIError, match="unexpected"):
        await crm.list_records(client, "om", "Leads")


# ---- Books -----------------------------------------------------------------

def _books(params):
    return {**params, "organization_id": BOOKS_ORG_ID}


async def test_books_search_contacts_filters_output(client, router):
    contact = {"contact_id": "1", "contact_name": "A", "company_name": "B", "email": "a@b.de",
               "phone": "1", "status": "active", "outstanding_receivable_amount": 5,
               "currency_code": "EUR", "$x": 1, "billing_address": {"street": "S"},
               "contact_persons": [{"email": "p@b.de"}], "notes": "private", "iban": "DE00"}
    route = _ok(router, "/books/v3/contacts", _books({"search_text": "Acme & Co", **_paging()}), {
        "code": 0, "contacts": [contact],
        "page_context": {"page": 1, "per_page": 50, "has_more_page": True}})
    out = await books.search_contacts(client, "Acme & Co", page=1)
    assert _params(route) == {"search_text": "Acme & Co", "page": "1", "per_page": "50",
                              "organization_id": BOOKS_ORG_ID}
    assert out == {"data": [{"contact_id": "1", "contact_name": "A", "company_name": "B",
                             "email": "a@b.de", "phone": "1", "status": "active",
                             "outstanding_receivable_amount": 5, "currency_code": "EUR"}],
                   "page": 1, "has_more_page": True}


async def test_books_get_contact_filters_output(client, router):
    _ok(router, "/books/v3/contacts/321", _books({}), {
        "code": 0, "contact": {"contact_id": "321", "contact_name": "N", "website": "w",
                               "custom_fields": [{"v": 1}]}})
    out = await books.get_contact(client, "321")
    assert out == {"data": {"contact_id": "321", "contact_name": "N"}}


async def test_books_get_contact_missing_is_none(client, router):
    _ok(router, "/books/v3/contacts/322", _books({}), {"code": 0})
    assert await books.get_contact(client, "322") == {"data": None}


ESTIMATE = {
    "estimate_id": "e1", "estimate_number": "EST-1", "date": "2026-01-01",
    "expiry_date": "2026-02-01", "status": "sent", "customer_id": "7", "customer_name": "C",
    "total": 119, "currency_code": "EUR", "reference_number": "R", "notes": "private",
    "billing_address": {"street": "S"}, "salesperson_name": "S", "email": "c@d.de",
    "line_items": [{"line_item_id": "9", "name": "Web", "description": "d", "quantity": 1,
                    "rate": 100, "item_total": 100, "item_id": "5", "tax_id": "3",
                    "item_custom_fields": [1]}]}
SLIM_ESTIMATE = {
    "estimate_id": "e1", "estimate_number": "EST-1", "date": "2026-01-01",
    "expiry_date": "2026-02-01", "status": "sent", "customer_id": "7", "customer_name": "C",
    "total": 119, "currency_code": "EUR", "reference_number": "R",
    "line_items": [{"name": "Web", "description": "d", "quantity": 1, "rate": 100,
                    "item_total": 100}]}


async def test_books_list_estimates_filters_output(client, router):
    params = _books({"status": "sent", "customer_id": "777", **_paging(2)})
    route = _ok(router, "/books/v3/estimates", params, {
        "estimates": [ESTIMATE], "page_context": {"page": 2, "has_more_page": False}})
    out = await books.list_estimates(client, status="sent", customer_id="777", page=2)
    assert _params(route) == {k: str(v) for k, v in params.items()}
    assert out == {"data": [SLIM_ESTIMATE], "page": 2, "has_more_page": False}


async def test_books_list_estimates_without_filters(client, router):
    route = _ok(router, "/books/v3/estimates", _books(_paging()), {"estimates": []})
    out = await books.list_estimates(client)
    assert set(_params(route)) == {"page", "per_page", "organization_id"}
    assert out["has_more_page"] is False and out["page"] == 1


async def test_books_get_estimate_filters_output(client, router):
    _ok(router, "/books/v3/estimates/9", _books({}), {"estimate": ESTIMATE})
    assert await books.get_estimate(client, "9") == {"data": SLIM_ESTIMATE}


@pytest.mark.parametrize("body", [{"estimate": "x"}, {"estimate": [1]},
                                  {"estimate": {"line_items": "x"}},
                                  {"estimate": {"line_items": [1]}}])
async def test_books_unexpected_shapes_are_clean_errors(client, router, body):
    _ok(router, "/books/v3/estimates/9", _books({}), body)
    with pytest.raises(ZohoAPIError, match="unexpected"):
        await books.get_estimate(client, "9")


@pytest.mark.parametrize("bad", ["1/2", "..", "%2F", "1?x", "1&x", "1 2", "", "abc", None])
async def test_books_bad_ids_rejected(client, router, bad):
    with pytest.raises(ZohoRequestError):
        await books.get_contact(client, bad)
    with pytest.raises(ZohoRequestError):
        await books.get_estimate(client, bad)
    with pytest.raises(ZohoRequestError):
        await books.list_estimates(client, customer_id=bad if bad is not None else "x")
    assert not data_calls(router)


@pytest.mark.parametrize("status", ["sent&x=1", "a b", "", "x" * 40, "draft/../x", 5, "paid",
                                    "Draft"])
async def test_books_bad_status_rejected(client, router, status):
    with pytest.raises(ZohoRequestError):
        await books.list_estimates(client, status=status)
    assert not data_calls(router)


@pytest.mark.parametrize("page", [0, 101, True, "1", 1.2])
async def test_books_page_bounds(client, router, page):
    with pytest.raises(ZohoRequestError):
        await books.search_contacts(client, "x", page=page)
    with pytest.raises(ZohoRequestError):
        await books.list_estimates(client, page=page)
    assert not data_calls(router)


async def test_books_page_100_allowed(client, router):
    route = _ok(router, "/books/v3/contacts",
                _books({"search_text": "x", **_paging(100)}), {"contacts": []})
    await books.search_contacts(client, "x", page=100)
    assert _params(route)["page"] == "100"


@pytest.mark.parametrize("text", ["", None, "a" * 101, "a\x00"])
async def test_books_bad_search_text_rejected(client, router, text):
    with pytest.raises(ZohoRequestError):
        await books.search_contacts(client, text)
    assert not data_calls(router)


async def test_books_search_text_100_allowed(client, router):
    _ok(router, "/books/v3/contacts", _books({"search_text": "a" * 100, **_paging()}),
        {"contacts": []})
    assert (await books.search_contacts(client, "a" * 100))["data"] == []
