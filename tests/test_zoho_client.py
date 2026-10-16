"""Zoho client: GET-only allowlist, token handling per org, secret hygiene."""
import logging

import httpx
import pytest
import respx
from zoho_support import (
    ACCOUNTS,
    API,
    BOOKS_ORG_ID,
    ENV_BOTH,
    ENV_OM,
    SECRETS,
    TOKEN_URL,
    TokenMock,
    data_calls,
)

import integrations.zoho.client as zc
from integrations.zoho.client import (
    ZohoAPIError,
    ZohoClient,
    ZohoConfig,
    ZohoRequestError,
    strip_dollar_keys,
    validate_request,
)

LEAD = f"{API}/crm/v6/Leads/123"


@pytest.fixture
def router():
    with respx.mock(assert_all_called=False) as r:
        yield r


@pytest.fixture
def client():
    return ZohoClient(ZohoConfig.from_env(ENV_BOTH))


# ---- allowlist -------------------------------------------------------------

@pytest.mark.parametrize("method", ["POST", "PUT", "PATCH", "DELETE", "get", "HEAD"])
def test_only_get_is_allowed(method):
    with pytest.raises(ZohoRequestError):
        validate_request(method, "/crm/v6/Leads/123", {})


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
        await client.get("om", path)
    assert token.calls == 0 and not data_calls(router)


@pytest.mark.parametrize("path", [
    "/crm/v6/Leads/search", "/crm/v6/Deals", "/crm/v6/Contacts/42", "/crm/v6/Accounts/42/Notes",
    "/books/v3/contacts", "/books/v3/contacts/77", "/books/v3/estimates", "/books/v3/estimates/77",
])
def test_known_paths_pass(path):
    validate_request("GET", path, {})


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
        await client.get("om", "/books/v3/contacts", {"organization_id": "999"})
    assert not data_calls(router)


def test_client_has_no_write_verbs(client):
    for verb in ("post", "put", "patch", "delete", "request"):
        assert not hasattr(client, verb)


# ---- tokens ----------------------------------------------------------------

async def test_token_minted_with_refresh_grant_and_reused(client, router):
    token = TokenMock(router)
    route = router.get(LEAD).mock(return_value=httpx.Response(200, json={"data": [{"id": "123"}]}))
    await client.get("om", "/crm/v6/Leads/123")
    await client.get("om", "/crm/v6/Leads/123")
    assert token.calls == 1 and route.call_count == 2
    body = token.bodies[0]
    assert body["grant_type"] == "refresh_token"
    assert body["client_id"] == ENV_OM["ZOHO_OM_CLIENT_ID"]
    assert route.calls[0].request.headers["authorization"] == "Zoho-oauthtoken AT-om-1"
    assert str(route.calls[0].request.url).startswith(API)
    assert route.calls[0].request.extensions["timeout"]["read"] == 20


async def test_token_caches_are_separate_per_org(client, router):
    token = TokenMock(router)
    om = router.get(LEAD).mock(return_value=httpx.Response(200, json={"data": []}))
    ado = router.get(f"{API}/crm/v6/Contacts/5").mock(
        return_value=httpx.Response(200, json={"data": []}))
    await client.get("om", "/crm/v6/Leads/123")
    await client.get("ado", "/crm/v6/Contacts/5")
    await client.get("om", "/crm/v6/Leads/123")
    await client.get("ado", "/crm/v6/Contacts/5")
    assert token.calls == 2
    assert om.calls[0].request.headers["authorization"] == "Zoho-oauthtoken AT-om-1"
    assert ado.calls[0].request.headers["authorization"] == "Zoho-oauthtoken AT-ado-2"
    assert {b["client_id"] for b in token.bodies} == {
        ENV_BOTH["ZOHO_OM_CLIENT_ID"], ENV_BOTH["ZOHO_ADO_CLIENT_ID"]}


async def test_token_expires_after_50_minutes(client, router, monkeypatch):
    now = [1000.0]
    monkeypatch.setattr(zc.time, "monotonic", lambda: now[0])
    token = TokenMock(router)
    router.get(LEAD).mock(return_value=httpx.Response(200, json={"data": []}))
    await client.get("om", "/crm/v6/Leads/123")
    now[0] += 49 * 60
    await client.get("om", "/crm/v6/Leads/123")
    assert token.calls == 1
    now[0] += 2 * 60
    await client.get("om", "/crm/v6/Leads/123")
    assert token.calls == 2


async def test_401_triggers_one_remint_and_retry(client, router):
    token = TokenMock(router)
    route = router.get(LEAD).mock(side_effect=[
        httpx.Response(401, json={"code": "INVALID_TOKEN"}),
        httpx.Response(200, json={"data": [{"id": "123"}]})])
    out = await client.get("om", "/crm/v6/Leads/123")
    assert out == {"data": [{"id": "123"}]}
    assert token.calls == 2 and route.call_count == 2


async def test_persistent_401_raises_after_single_retry(client, router):
    token = TokenMock(router)
    route = router.get(LEAD).mock(return_value=httpx.Response(401, json={"code": "INVALID_TOKEN"}))
    with pytest.raises(ZohoAPIError):
        await client.get("om", "/crm/v6/Leads/123")
    assert token.calls == 2 and route.call_count == 2


async def test_unconfigured_org_rejected_without_network(router):
    only_om = ZohoClient(ZohoConfig.from_env(ENV_OM))
    token = TokenMock(router)
    with pytest.raises(ZohoRequestError):
        await only_om.get("ado", "/crm/v6/Leads/123")
    with pytest.raises(ZohoRequestError):
        await only_om.get("evil", "/crm/v6/Leads/123")
    assert token.calls == 0


# ---- books binding ---------------------------------------------------------

async def test_books_uses_om_token_and_env_organization(client, router):
    token = TokenMock(router)
    route = router.get(f"{API}/books/v3/contacts").mock(
        return_value=httpx.Response(200, json={"contacts": []}))
    await client.get("om", "/books/v3/contacts", {"search_text": "x"})
    req = route.calls[0].request
    assert req.url.params["organization_id"] == BOOKS_ORG_ID
    assert req.headers["authorization"] == "Zoho-oauthtoken AT-om-1"
    assert token.bodies[0]["client_id"] == ENV_OM["ZOHO_OM_CLIENT_ID"]


async def test_books_rejects_ado_org(client, router):
    token = TokenMock(router)
    with pytest.raises(ZohoRequestError):
        await client.get("ado", "/books/v3/contacts")
    assert token.calls == 0


async def test_books_requires_organization_id_in_env(router):
    no_books = ZohoClient(ZohoConfig.from_env(ENV_OM))
    token = TokenMock(router)
    with pytest.raises(ZohoRequestError):
        await no_books.get("om", "/books/v3/contacts")
    assert token.calls == 0


# ---- secret hygiene --------------------------------------------------------

def _assert_clean(text: str):
    for secret in SECRETS:
        assert secret not in text, f"secret leaked: {secret}"


async def test_token_refresh_failure_leaks_nothing(client, router, caplog):
    caplog.set_level(logging.DEBUG)
    router.post(TOKEN_URL).mock(return_value=httpx.Response(
        400, json={"error": "invalid_client", "echo": ENV_OM["ZOHO_OM_CLIENT_SECRET"]}))
    with pytest.raises(ZohoAPIError) as exc:
        await client.get("om", "/crm/v6/Leads/123")
    _assert_clean(str(exc.value) + repr(exc.value) + caplog.text)
    assert "invalid_client" in str(exc.value)


async def test_token_200_with_error_body_is_failure(client, router):
    router.post(TOKEN_URL).mock(return_value=httpx.Response(
        200, json={"error": "invalid_code"}))
    with pytest.raises(ZohoAPIError, match="invalid_code"):
        await client.get("om", "/crm/v6/Leads/123")


async def test_token_network_error_leaks_nothing(client, router, caplog):
    caplog.set_level(logging.DEBUG)
    router.post(TOKEN_URL).mock(side_effect=httpx.ConnectError(
        f"boom {ENV_OM['ZOHO_OM_REFRESH_TOKEN']} {ENV_OM['ZOHO_OM_CLIENT_SECRET']}"))
    with pytest.raises(ZohoAPIError) as exc:
        await client.get("om", "/crm/v6/Leads/123")
    _assert_clean(str(exc.value) + caplog.text)
    assert exc.value.__cause__ is None and exc.value.__suppress_context__


async def test_data_http_error_leaks_nothing(client, router, caplog):
    caplog.set_level(logging.DEBUG)
    TokenMock(router)
    router.get(LEAD).mock(return_value=httpx.Response(
        500, json={"code": "INTERNAL", "message": "failed for AT-om-1 and "
                   + ENV_OM["ZOHO_OM_CLIENT_SECRET"]}))
    with pytest.raises(ZohoAPIError) as exc:
        await client.get("om", "/crm/v6/Leads/123")
    _assert_clean(str(exc.value) + caplog.text)
    assert "500" in str(exc.value)


async def test_data_network_error_leaks_nothing(client, router, caplog):
    caplog.set_level(logging.DEBUG)
    TokenMock(router)
    router.get(LEAD).mock(side_effect=httpx.ReadTimeout("timeout AT-om-1"))
    with pytest.raises(ZohoAPIError) as exc:
        await client.get("om", "/crm/v6/Leads/123")
    _assert_clean(str(exc.value) + caplog.text)


async def test_invalid_json_body_is_clean_error(client, router):
    TokenMock(router)
    router.get(LEAD).mock(return_value=httpx.Response(200, text="<html>nope</html>"))
    with pytest.raises(ZohoAPIError):
        await client.get("om", "/crm/v6/Leads/123")


async def test_no_content_returns_none(client, router):
    TokenMock(router)
    router.get(LEAD).mock(return_value=httpx.Response(204))
    assert await client.get("om", "/crm/v6/Leads/123") is None


async def test_successful_run_logs_no_secret(client, router, caplog):
    caplog.set_level(logging.DEBUG)
    TokenMock(router)
    router.get(LEAD).mock(return_value=httpx.Response(200, json={"data": []}))
    await client.get("om", "/crm/v6/Leads/123")
    _assert_clean(caplog.text)


def test_config_repr_hides_secrets():
    _assert_clean(repr(ZohoConfig.from_env(ENV_BOTH)))


# ---- config ----------------------------------------------------------------

def test_config_defaults_and_overrides():
    cfg = ZohoConfig.from_env(ENV_OM)
    assert cfg.accounts_url == ACCOUNTS and cfg.api_domain == API
    cfg = ZohoConfig.from_env({**ENV_OM, "ZOHO_ACCOUNTS_URL": "https://accounts.zoho.com/",
                               "ZOHO_API_DOMAIN": "https://www.zohoapis.com"})
    assert cfg.accounts_url == "https://accounts.zoho.com"
    assert cfg.api_domain == "https://www.zohoapis.com"


@pytest.mark.parametrize("key,value", [
    ("ZOHO_ACCOUNTS_URL", "http://accounts.zoho.eu"),
    ("ZOHO_API_DOMAIN", "https://evil.example/path?x=1"),
    ("ZOHO_API_DOMAIN", "https://a.example@evil.example"),
])
def test_config_rejects_unsafe_urls(key, value):
    with pytest.raises(ValueError):
        ZohoConfig.from_env({**ENV_OM, key: value})


def test_partial_credentials_do_not_count():
    partial = {"ZOHO_OM_CLIENT_ID": "a", "ZOHO_OM_CLIENT_SECRET": "b",
               "ZOHO_ADO_REFRESH_TOKEN": "c", "ZOHO_ADO_CLIENT_ID": "d"}
    assert ZohoConfig.from_env(partial).crm_orgs == ()


def test_books_enabled_needs_om_and_valid_org_id():
    assert ZohoConfig.from_env(ENV_BOTH).books_enabled
    assert not ZohoConfig.from_env(ENV_OM).books_enabled
    assert not ZohoConfig.from_env({**ENV_OM, "ZOHO_BOOKS_ORGANIZATION_ID": "12/../3"}).books_enabled
    ado_only = {k: v for k, v in ENV_BOTH.items() if not k.startswith("ZOHO_OM")}
    cfg = ZohoConfig.from_env(ado_only)
    assert cfg.crm_orgs == ("ado",) and not cfg.books_enabled


# ---- helpers ---------------------------------------------------------------

def test_strip_dollar_keys_recursive():
    raw = {"data": [{"id": "1", "$approved": True, "Owner": {"id": "2", "$x": 1}}], "$meta": 1,
           "info": {"page": 1}}
    assert strip_dollar_keys(raw) == {
        "data": [{"id": "1", "Owner": {"id": "2"}}], "info": {"page": 1}}
