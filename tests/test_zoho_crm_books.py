"""Zoho CRM and Books request builders: exact requests, validation, injection safety."""
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


@pytest.fixture
def router():
    with respx.mock(assert_all_called=False) as r:
        yield r


@pytest.fixture
def client(router):
    TokenMock(router)
    return ZohoClient(ZohoConfig.from_env(ENV_BOTH))


def _ok(router, path, body=None, status=200):
    return router.get(f"{API}{path}").mock(
        return_value=httpx.Response(status, json=body if body is not None else {"data": []}))


def _params(route):
    return dict(route.calls.last.request.url.params)


# ---- CRM search ------------------------------------------------------------

@pytest.mark.parametrize("by", ["word", "email", "phone"])
async def test_search_by_query_key(client, router, by):
    route = _ok(router, "/crm/v6/Leads/search", {
        "data": [{"id": "1", "$editable": True, "Full_Name": "A"}],
        "info": {"page": 1, "per_page": 50, "count": 1, "more_records": False}})
    out = await crm.search(client, "om", "Leads", by, "müller@example.com")
    assert _params(route) == {by: "müller@example.com", "page": "1", "per_page": "50"}
    assert out == {"data": [{"id": "1", "Full_Name": "A"}], "page": 1, "more_records": False}


@pytest.mark.parametrize("module,by,field", [
    ("Leads", "full_name", "Full_Name"), ("Leads", "company", "Company"),
    ("Contacts", "full_name", "Full_Name"), ("Contacts", "account", "Account_Name"),
    ("Accounts", "name", "Account_Name"), ("Deals", "name", "Deal_Name"),
])
async def test_field_search_mapping(client, router, module, by, field):
    route = _ok(router, f"/crm/v6/{module}/search")
    await crm.search(client, "om", module, by, "Acme", page=3)
    assert _params(route) == {"criteria": f"({field}:equals:Acme)", "page": "3", "per_page": "50"}


@pytest.mark.parametrize("module,by", [
    ("Leads", "account"), ("Leads", "name"), ("Contacts", "company"), ("Contacts", "name"),
    ("Accounts", "full_name"), ("Accounts", "company"), ("Deals", "full_name"),
    ("Deals", "account"), ("Leads", "Full_Name"), ("Leads", "word&x"), ("Leads", ""),
    ("Leads", "criteria"), ("Leads", "page"),
])
async def test_other_search_combinations_rejected(client, router, module, by):
    with pytest.raises(ZohoRequestError):
        await crm.search(client, "om", module, by, "x")
    assert not data_calls(router)


async def test_criteria_escaping(client, router):
    route = _ok(router, "/crm/v6/Leads/search")
    await crm.search(client, "om", "Leads", "company", r"A(b),c\d")
    assert _params(route)["criteria"] == r"(Company:equals:A\(b\)\,c\\d)"


@pytest.mark.parametrize("value", ["x&send=true", "x)or(", "x)or(Email:equals:a", "a=b&page=9",
                                   "x#frag", "x y", "%26", "x;y", "ü"])
@pytest.mark.parametrize("by", ["word", "company"])
async def test_value_injection_stays_in_one_parameter(client, router, by, value):
    route = _ok(router, "/crm/v6/Leads/search")
    await crm.search(client, "om", "Leads", by, value)
    url = route.calls.last.request.url
    assert sorted(url.params.keys()) == sorted(["criteria" if by == "company" else by,
                                                "page", "per_page"])
    assert "send" not in url.params
    assert "&send=true" not in str(url)
    assert url.params["page"] == "1"


@pytest.mark.parametrize("value", ["", " ", None, 5, "a" * 201, "a\x00b", "a\nb"])
async def test_bad_search_values_rejected(client, router, value):
    with pytest.raises(ZohoRequestError):
        await crm.search(client, "om", "Leads", "word", value)
    assert not data_calls(router)


async def test_search_no_content_is_empty_result(client, router):
    router.get(f"{API}/crm/v6/Leads/search").mock(return_value=httpx.Response(204))
    out = await crm.search(client, "om", "Leads", "word", "nobody")
    assert out == {"data": [], "page": 1, "more_records": False}


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
        await crm.search(client, "om", "Leads", "word", "x", page=page)
    with pytest.raises(ZohoRequestError):
        await crm.list_records(client, "om", "Leads", page=page)
    with pytest.raises(ZohoRequestError):
        await crm.list_notes(client, "om", "Leads", "1", page=page)
    assert not data_calls(router)


@pytest.mark.parametrize("page", [1, 40])
async def test_crm_page_edges_pass(client, router, page):
    route = _ok(router, "/crm/v6/Leads")
    await crm.list_records(client, "om", "Leads", page=page)
    assert _params(route)["page"] == str(page)


# ---- CRM get / list / notes ------------------------------------------------

async def test_get_record(client, router):
    route = _ok(router, "/crm/v6/Contacts/4711", {"data": [{"id": "4711", "$state": "save"}]})
    out = await crm.get_record(client, "ado", "Contacts", "4711")
    assert route.calls.last.request.url.query == b""
    assert out == {"data": [{"id": "4711"}]}


async def test_list_records_default_and_custom_fields(client, router):
    route = _ok(router, "/crm/v6/Deals", {
        "data": [{"id": "1"}], "info": {"page": 2, "more_records": True, "per_page": 50}})
    out = await crm.list_records(client, "om", "Deals", fields=["Deal_Name", "Stage"], page=2)
    assert _params(route) == {"fields": "Deal_Name,Stage", "page": "2", "per_page": "50"}
    assert out == {"data": [{"id": "1"}], "page": 2, "more_records": True}
    await crm.list_records(client, "om", "Deals")
    assert _params(route)["fields"] == crm.DEFAULT_LIST_FIELDS["Deals"]


@pytest.mark.parametrize("fields", [["A,B"], ["a b"], ["a&b=1"], [""], ["a" * 65], ["a-b"],
                                    ["x"] * 51, "Full_Name", [1], ["Owner.id"], []])
async def test_bad_fields_rejected(client, router, fields):
    with pytest.raises(ZohoRequestError):
        await crm.list_records(client, "om", "Leads", fields=fields)
    assert not data_calls(router)


async def test_fifty_fields_allowed(client, router):
    route = _ok(router, "/crm/v6/Leads")
    await crm.list_records(client, "om", "Leads", fields=[f"F{i}" for i in range(50)])
    assert len(_params(route)["fields"].split(",")) == 50


async def test_list_notes(client, router):
    route = _ok(router, "/crm/v6/Accounts/55/Notes", {
        "data": [{"id": "n1", "Note_Content": "hi"}],
        "info": {"page": 1, "more_records": False}})
    out = await crm.list_notes(client, "om", "Accounts", "55", page=1)
    assert _params(route) == {"page": "1", "per_page": "50"}
    assert out["data"][0]["Note_Content"] == "hi" and out["more_records"] is False


async def test_upstream_error_becomes_clean_error(client, router):
    _ok(router, "/crm/v6/Leads/9", {"code": "INVALID_DATA", "message": "nope"}, status=400)
    with pytest.raises(ZohoAPIError, match="400"):
        await crm.get_record(client, "om", "Leads", "9")


# ---- Books -----------------------------------------------------------------

async def test_books_search_contacts(client, router):
    route = _ok(router, "/books/v3/contacts", {
        "code": 0, "contacts": [{"contact_id": "1", "$x": 1}],
        "page_context": {"page": 1, "per_page": 50, "has_more_page": True}})
    out = await books.search_contacts(client, "Acme & Co", page=1)
    assert _params(route) == {"search_text": "Acme & Co", "page": "1", "per_page": "50",
                              "organization_id": BOOKS_ORG_ID}
    assert out == {"data": [{"contact_id": "1"}], "page": 1, "has_more_page": True}


async def test_books_get_contact(client, router):
    route = _ok(router, "/books/v3/contacts/321", {"code": 0, "contact": {"contact_id": "321"}})
    out = await books.get_contact(client, "321")
    assert _params(route) == {"organization_id": BOOKS_ORG_ID}
    assert out == {"data": {"contact_id": "321"}}


async def test_books_list_estimates(client, router):
    route = _ok(router, "/books/v3/estimates", {
        "estimates": [{"estimate_id": "e1"}],
        "page_context": {"page": 2, "has_more_page": False}})
    out = await books.list_estimates(client, status="sent", customer_id="777", page=2)
    assert _params(route) == {"status": "sent", "customer_id": "777", "page": "2",
                              "per_page": "50", "organization_id": BOOKS_ORG_ID}
    assert out == {"data": [{"estimate_id": "e1"}], "page": 2, "has_more_page": False}


async def test_books_list_estimates_without_filters(client, router):
    route = _ok(router, "/books/v3/estimates", {"estimates": []})
    out = await books.list_estimates(client)
    assert set(_params(route)) == {"page", "per_page", "organization_id"}
    assert out["has_more_page"] is False and out["page"] == 1


async def test_books_get_estimate(client, router):
    _ok(router, "/books/v3/estimates/9", {"estimate": {"estimate_id": "9"}})
    assert await books.get_estimate(client, "9") == {"data": {"estimate_id": "9"}}


@pytest.mark.parametrize("bad", ["1/2", "..", "%2F", "1?x", "1&x", "1 2", "", "abc", None])
async def test_books_bad_ids_rejected(client, router, bad):
    with pytest.raises(ZohoRequestError):
        await books.get_contact(client, bad)
    with pytest.raises(ZohoRequestError):
        await books.get_estimate(client, bad)
    with pytest.raises(ZohoRequestError):
        await books.list_estimates(client, customer_id=bad if bad is not None else "x")
    assert not data_calls(router)


@pytest.mark.parametrize("status", ["sent&x=1", "a b", "", "x" * 40, "draft/../x", 5])
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
    route = _ok(router, "/books/v3/contacts", {"contacts": []})
    await books.search_contacts(client, "x", page=100)
    assert _params(route)["page"] == "100"


@pytest.mark.parametrize("text", ["", None, "a" * 201, "a\x00"])
async def test_books_bad_search_text_rejected(client, router, text):
    with pytest.raises(ZohoRequestError):
        await books.search_contacts(client, text)
    assert not data_calls(router)


async def test_books_has_no_other_surface():
    public = {n for n in dir(books) if not n.startswith("_") and callable(getattr(books, n))
              and getattr(getattr(books, n), "__module__", "") == books.__name__}
    assert public == {"search_contacts", "get_contact", "list_estimates", "get_estimate"}
