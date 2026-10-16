"""Zoho client: GET-only allowlist, org binding, secret hygiene, config."""
import logging

import httpx
import pytest
import respx
from zoho_support import (
    ACCOUNTS,
    API,
    BOOKS_ORG_ID,
    CONTACT_PARAMS,
    ENV_BOTH,
    ENV_OM,
    LEAD_PARAMS,
    SECRETS,
    TOKEN_URL,
    TokenMock,
    data_calls,
)

from integrations.zoho.client import (
    ZohoAPIError,
    ZohoClient,
    ZohoConfig,
    ZohoRequestError,
    strip_dollar_keys,
    validate_request,
)
from integrations.zoho.validation import MODULE_FIELDS

LEAD = f"{API}/crm/v6/Leads/123"


@pytest.fixture
def router():
    with respx.mock(assert_all_called=False) as r:
        yield r


@pytest.fixture
def client():
    return ZohoClient(ZohoConfig.from_env(ENV_BOTH))


def mock_lead(router, *responses, **kwargs):
    """Mock the lead endpoint with its exact mandatory query."""
    route = router.get(LEAD, params=LEAD_PARAMS)
    if responses:
        route.mock(side_effect=list(responses))
    else:
        route.mock(**kwargs)
    return route


async def get_lead(client, org="om"):
    return await client.get(org, "/crm/v6/Leads/123", LEAD_PARAMS)


# ---- allowlist -------------------------------------------------------------

@pytest.mark.parametrize("method", ["POST", "PUT", "PATCH", "DELETE", "get", "HEAD"])
def test_only_get_is_allowed(method):
    with pytest.raises(ZohoRequestError):
        validate_request(method, "/crm/v6/Leads/123", LEAD_PARAMS)


@pytest.mark.parametrize("path", [
    "/crm/v6/Users", "/crm/v6/Leads/", "/crm/v6/Leads/1/../2", "/crm/v6/Leads/1%2F2",
    "/crm/v6/Leads/12?x=1", "/crm/v6/Leads/1&x=2", "/crm/v6/Leads/ 1", "/crm/v6/Leads/1\n",
    "/crm/v6/Leads/1/Notes/", "/crm/v6/Leads/1/Attachments", "/crm/v6/Leads/123/actions/send_mail",
    "/crm/v6/leads/1", "/crm/v2/Leads/1", "/crm/v6/Leads/" + "1" * 26, "/crm/v6/Leads/١٢",
    "/books/v3/items", "/books/v3/settings/taxes", "/books/v3/contacts/1/email",
    "/books/v3/estimates/1/email", "/books/v3/invoices", "/books/v3/contacts/1/..",
    "//crm/v6/Leads/1", "crm/v6/Leads/1", "https://evil.example/crm/v6/Leads/1",
])
async def test_bad_paths_rejected_before_network(path, client, router):
    token = TokenMock(router)
    with pytest.raises(ZohoRequestError):
        await client.get("om", path, LEAD_PARAMS)
    assert token.calls == 0 and not data_calls(router)


@pytest.mark.parametrize("path,params", [
    ("/crm/v6/Leads/search", {"word": "ab", "page": 1, "per_page": 50}),
    ("/crm/v6/Deals", {"fields": MODULE_FIELDS["Deals"], "page": 1, "per_page": 50}),
    ("/crm/v6/Contacts/42", CONTACT_PARAMS),
    ("/crm/v6/Accounts/42/Notes", {"fields": "Note_Title,Note_Content,Created_Time,"
                                             "Modified_Time,Owner", "page": 1, "per_page": 50}),
    ("/books/v3/contacts", {"search_text": "x", "page": 1, "per_page": 50}),
    ("/books/v3/contacts/77", {}),
    ("/books/v3/estimates", {"page": 1, "per_page": 50}),
    ("/books/v3/estimates/77", {}),
])
def test_known_paths_pass_with_their_mandatory_query(path, params):
    validate_request("GET", path, params)


@pytest.mark.parametrize("params", [
    {"send": "true"}, {"organization_id": "1"}, {"fields": "A", "x": "1"}, {"page": True},
    {"page": 1.5}, {"page": None}, {"page": [1]},
])
async def test_unknown_or_badly_typed_query_rejected(params, client, router):
    token = TokenMock(router)
    with pytest.raises(ZohoRequestError):
        await client.get("om", "/crm/v6/Leads", params)
    assert token.calls == 0 and not data_calls(router)


async def test_caller_cannot_set_books_organization(client, router):
    TokenMock(router)
    with pytest.raises(ZohoRequestError):
        await client.get("om", "/books/v3/contacts", {
            "search_text": "x", "page": 1, "per_page": 50, "organization_id": "999"})
    assert not data_calls(router)


# ---- books binding ---------------------------------------------------------

async def test_books_uses_om_token_and_env_organization(client, router):
    token = TokenMock(router)
    route = router.get(f"{API}/books/v3/contacts",
                       params={"search_text": "x", "page": "1", "per_page": "50",
                               "organization_id": BOOKS_ORG_ID}).mock(
        return_value=httpx.Response(200, json={"contacts": []}))
    await client.get("om", "/books/v3/contacts",
                     {"search_text": "x", "page": 1, "per_page": 50})
    req = route.calls[0].request
    assert req.url.params["organization_id"] == BOOKS_ORG_ID
    assert req.headers["authorization"] == "Zoho-oauthtoken AT-om-1"
    assert token.bodies[0]["client_id"] == ENV_OM["ZOHO_OM_CLIENT_ID"]


async def test_books_rejects_ado_org(client, router):
    token = TokenMock(router)
    with pytest.raises(ZohoRequestError):
        await client.get("ado", "/books/v3/contacts/1")
    assert token.calls == 0


async def test_books_requires_organization_id_in_env(router):
    no_books = ZohoClient(ZohoConfig.from_env(ENV_OM))
    token = TokenMock(router)
    with pytest.raises(ZohoRequestError):
        await no_books.get("om", "/books/v3/contacts/1")
    assert token.calls == 0


async def test_unconfigured_org_rejected_without_network(router):
    only_om = ZohoClient(ZohoConfig.from_env(ENV_OM))
    token = TokenMock(router)
    with pytest.raises(ZohoRequestError):
        await get_lead(only_om, "ado")
    with pytest.raises(ZohoRequestError):
        await get_lead(only_om, "evil")
    assert token.calls == 0


async def test_token_caches_are_separate_per_org(client, router):
    token = TokenMock(router)
    om = mock_lead(router, return_value=httpx.Response(200, json={"data": []}))
    ado = router.get(f"{API}/crm/v6/Contacts/5", params=CONTACT_PARAMS).mock(
        return_value=httpx.Response(200, json={"data": []}))
    for _ in range(2):
        await get_lead(client)
        await client.get("ado", "/crm/v6/Contacts/5", CONTACT_PARAMS)
    assert token.calls == 2
    assert om.calls[0].request.headers["authorization"] == "Zoho-oauthtoken AT-om-1"
    assert ado.calls[0].request.headers["authorization"] == "Zoho-oauthtoken AT-ado-2"
    assert {b["client_id"] for b in token.bodies} == {
        ENV_BOTH["ZOHO_OM_CLIENT_ID"], ENV_BOTH["ZOHO_ADO_CLIENT_ID"]}


async def test_token_minted_with_refresh_grant_and_reused(client, router):
    token = TokenMock(router)
    route = mock_lead(router, return_value=httpx.Response(200, json={"data": [{"id": "123"}]}))
    await get_lead(client)
    await get_lead(client)
    assert token.calls == 1 and route.call_count == 2
    assert token.bodies[0]["grant_type"] == "refresh_token"
    assert token.bodies[0]["client_id"] == ENV_OM["ZOHO_OM_CLIENT_ID"]
    assert route.calls[0].request.headers["authorization"] == "Zoho-oauthtoken AT-om-1"
    assert str(route.calls[0].request.url).startswith(API)
    assert route.calls[0].request.extensions["timeout"]["read"] == 20


# ---- secret hygiene --------------------------------------------------------

def _assert_clean(text: str):
    for secret in SECRETS:
        assert secret not in text, f"secret leaked: {secret}"


async def test_token_refresh_failure_leaks_nothing(client, router, caplog):
    caplog.set_level(logging.DEBUG)
    router.post(TOKEN_URL).mock(return_value=httpx.Response(
        400, json={"error": "invalid_client", "echo": ENV_OM["ZOHO_OM_CLIENT_SECRET"]}))
    with pytest.raises(ZohoAPIError) as exc:
        await get_lead(client)
    _assert_clean(str(exc.value) + repr(exc.value) + caplog.text)
    assert "invalid_client" in str(exc.value)


async def test_token_200_with_error_body_is_failure(client, router):
    router.post(TOKEN_URL).mock(return_value=httpx.Response(200, json={"error": "invalid_code"}))
    with pytest.raises(ZohoAPIError, match="invalid_code"):
        await get_lead(client)


async def test_token_network_error_leaks_nothing(client, router, caplog):
    caplog.set_level(logging.DEBUG)
    router.post(TOKEN_URL).mock(side_effect=httpx.ConnectError(
        f"boom {ENV_OM['ZOHO_OM_REFRESH_TOKEN']} {ENV_OM['ZOHO_OM_CLIENT_SECRET']}"))
    with pytest.raises(ZohoAPIError) as exc:
        await get_lead(client)
    _assert_clean(str(exc.value) + caplog.text)
    assert exc.value.__cause__ is None and exc.value.__suppress_context__


async def test_data_http_error_leaks_nothing(client, router, caplog):
    caplog.set_level(logging.DEBUG)
    TokenMock(router)
    mock_lead(router, return_value=httpx.Response(
        500, json={"code": "INTERNAL", "message": "failed for AT-om-1 and "
                   + ENV_OM["ZOHO_OM_CLIENT_SECRET"]}))
    with pytest.raises(ZohoAPIError) as exc:
        await get_lead(client)
    _assert_clean(str(exc.value) + caplog.text)
    assert "500" in str(exc.value)


async def test_data_network_error_leaks_nothing(client, router, caplog):
    caplog.set_level(logging.DEBUG)
    TokenMock(router)
    mock_lead(router, side_effect=httpx.ReadTimeout("timeout AT-om-1"))
    with pytest.raises(ZohoAPIError) as exc:
        await get_lead(client)
    _assert_clean(str(exc.value) + caplog.text)


async def test_no_content_returns_none(client, router):
    TokenMock(router)
    mock_lead(router, return_value=httpx.Response(204))
    assert await get_lead(client) is None


async def test_successful_run_logs_no_secret(client, router, caplog):
    caplog.set_level(logging.DEBUG)
    TokenMock(router)
    mock_lead(router, return_value=httpx.Response(200, json={"data": []}))
    await get_lead(client)
    _assert_clean(caplog.text)


def test_config_repr_hides_secrets():
    _assert_clean(repr(ZohoConfig.from_env(ENV_BOTH)))


# ---- config ----------------------------------------------------------------

def test_config_defaults_are_the_eu_hosts():
    cfg = ZohoConfig.from_env(ENV_OM)
    assert cfg.accounts_url == ACCOUNTS and cfg.api_domain == API


def test_config_accepts_exact_hosts_with_trailing_slash():
    cfg = ZohoConfig.from_env({**ENV_OM, "ZOHO_ACCOUNTS_URL": ACCOUNTS + "/",
                               "ZOHO_API_DOMAIN": API})
    assert cfg.accounts_url == ACCOUNTS and cfg.api_domain == API


@pytest.mark.parametrize("key,value", [
    ("ZOHO_ACCOUNTS_URL", "http://accounts.zoho.eu"),
    ("ZOHO_ACCOUNTS_URL", "https://accounts.zoho.com"),
    ("ZOHO_ACCOUNTS_URL", "https://accounts.zoho.eu.evil.example"),
    ("ZOHO_ACCOUNTS_URL", "https://accounts.zoho.eu:8443"),
    ("ZOHO_ACCOUNTS_URL", "https://accounts.zoho.eu/path"),
    ("ZOHO_API_DOMAIN", "https://www.zohoapis.com"),
    ("ZOHO_API_DOMAIN", "https://evil.example/path?x=1"),
    ("ZOHO_API_DOMAIN", "https://a.example@evil.example"),
    ("ZOHO_API_DOMAIN", "https://www.zohoapis.eu@evil.example"),
])
def test_config_rejects_any_host_but_the_eu_ones(key, value):
    with pytest.raises(ValueError) as exc:
        ZohoConfig.from_env({**ENV_OM, key: value})
    assert value not in str(exc.value)


def test_partial_credentials_do_not_count():
    partial = {"ZOHO_OM_CLIENT_ID": "a", "ZOHO_OM_CLIENT_SECRET": "b",
               "ZOHO_ADO_REFRESH_TOKEN": "c", "ZOHO_ADO_CLIENT_ID": "d"}
    assert ZohoConfig.from_env(partial).crm_orgs == ()


def test_books_enabled_needs_om_and_valid_org_id():
    assert ZohoConfig.from_env(ENV_BOTH).books_enabled
    assert not ZohoConfig.from_env(ENV_OM).books_enabled
    ado_only = {k: v for k, v in ENV_BOTH.items() if not k.startswith("ZOHO_OM")}
    cfg = ZohoConfig.from_env(ado_only)
    assert cfg.crm_orgs == ("ado",) and not cfg.books_enabled


def test_invalid_books_org_id_warns_without_value(caplog):
    caplog.set_level(logging.WARNING)
    bad = "12/../3"
    cfg = ZohoConfig.from_env({**ENV_OM, "ZOHO_BOOKS_ORGANIZATION_ID": bad})
    assert not cfg.books_enabled
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert any("ZOHO_BOOKS_ORGANIZATION_ID" in r.getMessage() for r in warnings)
    assert bad not in caplog.text


def test_logger_name_is_namespaced(caplog):
    caplog.set_level(logging.WARNING)
    ZohoConfig.from_env({"ZOHO_OM_CLIENT_ID": "a"})
    assert {r.name for r in caplog.records} == {"odin.integrations.zoho"}


# ---- helpers ---------------------------------------------------------------

def test_strip_dollar_keys_recursive():
    raw = {"data": [{"id": "1", "$approved": True, "Owner": {"id": "2", "$x": 1}}], "$meta": 1,
           "info": {"page": 1}}
    assert strip_dollar_keys(raw) == {
        "data": [{"id": "1", "Owner": {"id": "2"}}], "info": {"page": 1}}
