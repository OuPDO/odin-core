"""The client itself enforces required params, bounds and formats (callers can be buggy)."""
import httpx
import pytest
import respx
from zoho_support import API, ENV_BOTH, TokenMock, data_calls

from integrations.zoho.client import ZohoClient, ZohoConfig, ZohoRequestError

NOTES_FIELDS = "Note_Title,Note_Content,Created_Time,Modified_Time,Owner"
SEARCH = "/crm/v6/Leads/search"
LIST = "/crm/v6/Deals"
GET = "/crm/v6/Contacts/42"
NOTES = "/crm/v6/Accounts/42/Notes"
B_CONTACTS = "/books/v3/contacts"
B_ESTIMATES = "/books/v3/estimates"
PAGING = {"page": 1, "per_page": 50}


@pytest.fixture
def router():
    with respx.mock(assert_all_called=False) as r:
        yield r


@pytest.fixture
def client():
    return ZohoClient(ZohoConfig.from_env(ENV_BOTH))


def _p(**kwargs):
    return {**PAGING, **kwargs}


REJECTED = [
    # required params
    (SEARCH, PAGING),                                              # no search key
    (SEARCH, {"word": "ab", "per_page": 50}),                      # no page
    (SEARCH, {"word": "ab", "page": 1}),                           # no per_page
    (SEARCH, _p(word="ab", email="a@b.de")),                       # two search keys
    (LIST, PAGING),                                                # no fields
    (LIST, {"fields": "A", "per_page": 50}),
    (GET, {}),                                                     # fields mandatory
    (NOTES, PAGING),                                               # fields mandatory
    (NOTES, {"fields": NOTES_FIELDS, "per_page": 50}),
    (B_CONTACTS, PAGING),                                          # search_text mandatory
    (B_CONTACTS, {"search_text": "x", "per_page": 50}),
    (B_ESTIMATES, {"per_page": 50}),
    (B_ESTIMATES, {"page": 1}),
    # page bounds, CRM 1..40, Books 1..100
    (SEARCH, {"word": "ab", "page": 0, "per_page": 50}),
    (SEARCH, {"word": "ab", "page": 41, "per_page": 50}),
    (LIST, {"fields": "A", "page": 41, "per_page": 50}),
    (NOTES, {"fields": NOTES_FIELDS, "page": 41, "per_page": 50}),
    (B_CONTACTS, {"search_text": "x", "page": 101, "per_page": 50}),
    (B_ESTIMATES, {"page": 0, "per_page": 50}),
    (B_ESTIMATES, {"page": "1", "per_page": 50}),
    # per_page fixed at 50
    (SEARCH, {"word": "ab", "page": 1, "per_page": 51}),
    (SEARCH, {"word": "ab", "page": 1, "per_page": 200}),
    (LIST, {"fields": "A", "page": 1, "per_page": 10}),
    (LIST, {"fields": "A", "page": 1, "per_page": "50"}),
    (B_ESTIMATES, {"page": 1, "per_page": 100}),
    (B_CONTACTS, {"search_text": "x", "page": 1, "per_page": 0}),
    # fields format and limits
    (LIST, _p(fields="")), (LIST, _p(fields="A B")), (LIST, _p(fields="A,")),
    (LIST, _p(fields=",A")), (LIST, _p(fields="A,,B")), (LIST, _p(fields="a-b")),
    (LIST, _p(fields="A&x=1")), (LIST, _p(fields="a" * 65)),
    (LIST, _p(fields=",".join(f"F{i}" for i in range(51)))),
    (GET, {"fields": "Owner.id"}), (GET, {"fields": "A\n"}),
    # notes fields are fixed
    (NOTES, _p(fields="Note_Content")),
    (NOTES, _p(fields=NOTES_FIELDS + ",Extra")),
    # CRM search text: 2..200
    (SEARCH, _p(word="a")), (SEARCH, _p(word="a" * 201)), (SEARCH, _p(word="  ")),
    (SEARCH, _p(email="a" * 201)), (SEARCH, _p(phone="")), (SEARCH, _p(word="a\x00b")),
    (SEARCH, _p(word="ab\n")),
    # criteria: fixed field set, escaped value, 2..200 characters
    (SEARCH, _p(criteria="(Email:equals:abc)")),
    (SEARCH, _p(criteria="(Full_Name:starts_with:abc)")),
    (SEARCH, _p(criteria="Full_Name:equals:abc")),
    (SEARCH, _p(criteria="(Full_Name:equals:a)")),
    (SEARCH, _p(criteria="(Full_Name:equals:" + "a" * 201 + ")")),
    (SEARCH, _p(criteria="(Full_Name:equals:ab)or(Email:equals:x)")),
    (SEARCH, _p(criteria="(Full_Name:equals:ab(c)")),
    (SEARCH, _p(criteria="(Full_Name:equals:ab,c)")),
    (SEARCH, _p(criteria="(Full_Name:equals:ab\\)")),
    # Books text, status, customer
    (B_CONTACTS, _p(search_text="x" * 101)), (B_CONTACTS, _p(search_text="")),
    (B_CONTACTS, _p(search_text="a\x00")),
    (B_ESTIMATES, _p(status="paid")), (B_ESTIMATES, _p(status="Draft")),
    (B_ESTIMATES, _p(status="sent&x=1")), (B_ESTIMATES, _p(status="")),
    (B_ESTIMATES, _p(customer_id="1/2")), (B_ESTIMATES, _p(customer_id="abc")),
    (B_ESTIMATES, _p(customer_id="")),
]


@pytest.mark.parametrize("path,params", REJECTED)
async def test_client_rejects_before_any_network(path, params, client, router):
    token = TokenMock(router)
    with pytest.raises(ZohoRequestError):
        await client.get("om", path, params)
    assert token.calls == 0 and not data_calls(router)


ACCEPTED = [
    (SEARCH, _p(word="ab")), (SEARCH, _p(email="x" * 200)), (SEARCH, _p(phone="12")),
    (SEARCH, {"word": "ab", "page": 40, "per_page": 50}),
    (SEARCH, _p(criteria="(Full_Name:equals:Mü\\(ll\\)er\\,\\\\)")),
    (SEARCH, _p(criteria="(Deal_Name:equals:ab)")),
    (LIST, _p(fields="A")),
    (LIST, _p(fields=",".join(f"F{i}" for i in range(50)))),
    (GET, {"fields": "Full_Name,Owner"}),
    (NOTES, _p(fields=NOTES_FIELDS)),
    (B_CONTACTS, _p(search_text="x" * 100)),
    (B_CONTACTS, {"search_text": "x", "page": 100, "per_page": 50}),
    (B_ESTIMATES, PAGING),
    (B_ESTIMATES, _p(customer_id="123", status="draft")),
]


@pytest.mark.parametrize("path,params", ACCEPTED)
async def test_client_accepts_valid_requests(path, params, client, router):
    TokenMock(router)
    route = router.get(f"{API}{path}").mock(return_value=httpx.Response(200, json={}))
    await client.get("om", path, params)
    assert route.call_count == 1


@pytest.mark.parametrize("status", ["draft", "sent", "invoiced", "accepted", "declined",
                                    "expired"])
async def test_every_estimate_status_is_accepted(status, client, router):
    TokenMock(router)
    route = router.get(f"{API}{B_ESTIMATES}", params={
        "status": status, "page": "1", "per_page": "50",
        "organization_id": "900000000123"}).mock(return_value=httpx.Response(200, json={}))
    await client.get("om", B_ESTIMATES, _p(status=status))
    assert route.call_count == 1
