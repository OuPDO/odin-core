"""Token minting: single flight per org, 401 policy, TTL from expires_in, failure backoff."""
import asyncio

import httpx
import pytest
import respx
from zoho_support import API, ENV_BOTH, TOKEN_URL, FakeClock, TokenMock, data_calls

from integrations.zoho.client import ZohoAPIError, ZohoClient, ZohoConfig

LEAD = f"{API}/crm/v6/Leads/123"
PARAMS = {"fields": "Full_Name"}
OK = {"data": [{"id": "123"}]}
CONCURRENCY = 11


@pytest.fixture
def router():
    with respx.mock(assert_all_called=False) as r:
        yield r


@pytest.fixture
def client():
    return ZohoClient(ZohoConfig.from_env(ENV_BOTH))


def mock_lead(router, **kwargs):
    return router.get(LEAD, params=PARAMS).mock(**kwargs)


async def get_lead(client, org="om"):
    return await client.get(org, "/crm/v6/Leads/123", PARAMS)


def invalid_token(code="INVALID_TOKEN"):
    return httpx.Response(401, json={"code": code, "status": "error"})


# ---- single flight ---------------------------------------------------------

async def test_concurrent_cold_cache_calls_mint_once(client, router):
    token = TokenMock(router, delay=0.05)
    route = mock_lead(router, return_value=httpx.Response(200, json=OK))
    results = await asyncio.gather(*[get_lead(client) for _ in range(CONCURRENCY)])
    assert token.calls == 1 and route.call_count == CONCURRENCY
    assert all(r == OK for r in results)
    assert {c.request.headers["authorization"] for c in route.calls} == {
        "Zoho-oauthtoken AT-om-1"}


async def test_concurrent_calls_after_expiry_mint_once(client, router, monkeypatch):
    clock = FakeClock(monkeypatch)
    token = TokenMock(router, delay=0.05)
    mock_lead(router, return_value=httpx.Response(200, json=OK))
    await get_lead(client)
    clock.advance(51 * 60)
    await asyncio.gather(*[get_lead(client) for _ in range(CONCURRENCY)])
    assert token.calls == 2


async def test_orgs_do_not_block_each_other(client, router):
    token = TokenMock(router, delay=0.05)
    mock_lead(router, return_value=httpx.Response(200, json=OK))
    router.get(f"{API}/crm/v6/Contacts/5", params=PARAMS).mock(
        return_value=httpx.Response(200, json=OK))
    await asyncio.gather(get_lead(client),
                         client.get("ado", "/crm/v6/Contacts/5", PARAMS))
    assert token.calls == 2


# ---- 401 policy ------------------------------------------------------------

@pytest.mark.parametrize("code", ["INVALID_TOKEN", "INVALID_OAUTHTOKEN"])
async def test_crm_invalid_token_codes_remint_and_retry(client, router, code):
    token = TokenMock(router)
    route = mock_lead(router, side_effect=[invalid_token(code), httpx.Response(200, json=OK)])
    assert await get_lead(client) == OK
    assert token.calls == 2 and route.call_count == 2
    assert route.calls[1].request.headers["authorization"] == "Zoho-oauthtoken AT-om-2"


@pytest.mark.parametrize("code", [14, 57])
async def test_books_invalid_token_codes_remint_and_retry(client, router, code):
    token = TokenMock(router)
    route = router.get(f"{API}/books/v3/estimates/9").mock(side_effect=[
        httpx.Response(401, json={"code": code, "message": "x"}),
        httpx.Response(200, json={"estimate": {"estimate_id": "9"}})])
    out = await client.get("om", "/books/v3/estimates/9")
    assert out == {"estimate": {"estimate_id": "9"}}
    assert token.calls == 2 and route.call_count == 2


async def test_persistent_invalid_token_raises_after_single_retry(client, router):
    token = TokenMock(router)
    route = mock_lead(router, return_value=invalid_token())
    with pytest.raises(ZohoAPIError, match="401"):
        await get_lead(client)
    assert token.calls == 2 and route.call_count == 2


async def test_scope_mismatch_401_neither_remints_nor_retries(client, router):
    token = TokenMock(router)
    route = mock_lead(router, return_value=httpx.Response(
        401, json={"code": "OAUTH_SCOPE_MISMATCH", "message": "scope"}))
    with pytest.raises(ZohoAPIError, match="OAUTH_SCOPE_MISMATCH"):
        await get_lead(client)
    assert token.calls == 1 and route.call_count == 1


@pytest.mark.parametrize("response", [
    httpx.Response(401, json={"code": "SOMETHING_ELSE"}),
    httpx.Response(401, json={"code": 57.5}),
    httpx.Response(401, json={"code": 57}),  # Books code on a CRM endpoint
    httpx.Response(401, text="<html>unauthorized</html>"),
    httpx.Response(401, json=["INVALID_TOKEN"]),
    httpx.Response(401),
])
async def test_other_401s_raise_immediately_without_minting(client, router, response):
    token = TokenMock(router)
    route = mock_lead(router, return_value=response)
    with pytest.raises(ZohoAPIError):
        await get_lead(client)
    assert token.calls == 1 and route.call_count == 1


async def test_late_401_does_not_evict_a_fresh_replacement_token(client, router):
    token = TokenMock(router)
    started, release = asyncio.Event(), asyncio.Event()

    async def handler(request):
        used = request.headers["authorization"].rsplit(" ", 1)[1]
        if used == "AT-om-1":
            if not started.is_set():  # request A: stays in flight, fails late
                started.set()
                await release.wait()
            return invalid_token()
        return httpx.Response(200, json=OK)

    mock_lead(router, side_effect=handler)
    slow = asyncio.create_task(get_lead(client))
    await started.wait()
    assert await get_lead(client) == OK  # B re-mints AT-om-2 after its own 401
    assert token.calls == 2
    release.set()
    assert await slow == OK
    assert token.calls == 2  # A reused AT-om-2 instead of evicting it and minting again
    assert sum(1 for r in data_calls(router) if "AT-om-2" in r.headers["authorization"]) == 2


# ---- TTL from expires_in ---------------------------------------------------

@pytest.mark.parametrize("expires_in,cached_for", [
    (3600, 3000), (7200, 3000), (1000, 700), (None, 3000)])
async def test_ttl_is_min_of_50_minutes_and_expires_in_minus_300(
        client, router, monkeypatch, expires_in, cached_for):
    clock = FakeClock(monkeypatch)
    token = TokenMock(router, expires_in=expires_in)
    mock_lead(router, return_value=httpx.Response(200, json=OK))
    await get_lead(client)
    clock.advance(cached_for - 5)
    await get_lead(client)
    assert token.calls == 1
    clock.advance(10)
    await get_lead(client)
    assert token.calls == 2


@pytest.mark.parametrize("expires_in", [0, 120, 300, -5, "3600", True, [1]])
async def test_tiny_or_bogus_expires_in_never_caches_beyond_validity(
        client, router, monkeypatch, expires_in):
    clock = FakeClock(monkeypatch)
    token = TokenMock(router, expires_in=expires_in)
    mock_lead(router, return_value=httpx.Response(200, json=OK))
    await get_lead(client)
    clock.advance(1)
    await get_lead(client)
    # bogus values fall back to the 3600 default, small real values mean no caching
    assert token.calls == (1 if expires_in in ("3600", True, [1]) else 2)


# ---- failure backoff -------------------------------------------------------

async def test_failed_mint_backs_off_60_seconds_without_contacting_zoho(
        client, router, monkeypatch):
    clock = FakeClock(monkeypatch)
    route = router.post(TOKEN_URL).mock(
        return_value=httpx.Response(400, json={"error": "invalid_client"}))
    with pytest.raises(ZohoAPIError, match="invalid_client"):
        await get_lead(client)
    assert route.call_count == 1
    clock.advance(59)
    with pytest.raises(ZohoAPIError):
        await get_lead(client)
    assert route.call_count == 1 and not data_calls(router)
    clock.advance(2)
    with pytest.raises(ZohoAPIError, match="invalid_client"):
        await get_lead(client)
    assert route.call_count == 2


async def test_backoff_error_message_is_clean(client, router):
    router.post(TOKEN_URL).mock(return_value=httpx.Response(500, text="boom"))
    for _ in range(2):
        with pytest.raises(ZohoAPIError) as exc:
            await get_lead(client)
        assert "AT-" not in str(exc.value)


async def test_concurrent_calls_during_failing_mint_hit_zoho_once(client, router):
    route = router.post(TOKEN_URL).mock(side_effect=_slow_failure)
    results = await asyncio.gather(*[get_lead(client) for _ in range(CONCURRENCY)],
                                   return_exceptions=True)
    assert all(isinstance(r, ZohoAPIError) for r in results)
    assert route.call_count == 1


async def _slow_failure(request):
    await asyncio.sleep(0.05)
    return httpx.Response(400, json={"error": "invalid_client"})


async def test_backoff_is_per_org(client, router):
    router.post(TOKEN_URL).mock(side_effect=_om_fails_ado_works)
    router.get(f"{API}/crm/v6/Contacts/5", params=PARAMS).mock(
        return_value=httpx.Response(200, json=OK))
    with pytest.raises(ZohoAPIError):
        await get_lead(client)
    assert await client.get("ado", "/crm/v6/Contacts/5", PARAMS) == OK


def _om_fails_ado_works(request):
    if b"om-refresh" in request.content:
        return httpx.Response(400, json={"error": "invalid_client"})
    return httpx.Response(200, json={"access_token": "AT-ado-1", "expires_in": 3600})


async def test_successful_mint_after_backoff_resets_state(client, router, monkeypatch):
    clock = FakeClock(monkeypatch)
    responses = [httpx.Response(400, json={"error": "invalid_client"}),
                 httpx.Response(200, json={"access_token": "AT-om-9", "expires_in": 3600})]
    router.post(TOKEN_URL).mock(side_effect=responses)
    mock_lead(router, return_value=httpx.Response(200, json=OK))
    with pytest.raises(ZohoAPIError):
        await get_lead(client)
    clock.advance(61)
    assert await get_lead(client) == OK
    assert await get_lead(client) == OK
