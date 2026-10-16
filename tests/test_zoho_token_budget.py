"""Hard mint budget (5 per rolling 10 minutes) and token-aware success confirmation."""
import asyncio
import random

import httpx
import pytest
import respx
from zoho_support import API, ENV_BOTH, LEAD_PARAMS, TOKEN_URL, FakeClock, TokenMock

from integrations.zoho.client import ZohoAPIError, ZohoClient, ZohoConfig

LEAD = f"{API}/crm/v6/Leads/123"
OK = {"data": [{"id": "123"}]}
BUDGET = 5
WINDOW = 600


@pytest.fixture
def router():
    with respx.mock(assert_all_called=False) as r:
        yield r


@pytest.fixture
def client():
    return ZohoClient(ZohoConfig.from_env(ENV_BOTH))


async def get_lead(client):
    return await client.get("om", "/crm/v6/Leads/123", LEAD_PARAMS)


def invalid_token():
    return httpx.Response(401, json={"code": "INVALID_TOKEN"})


# ---- hard budget, independent of the failure state machine -----------------

async def test_sixth_mint_in_ten_minutes_is_refused_without_contacting_zoho(
        client, router, monkeypatch):
    clock = FakeClock(monkeypatch)
    token = TokenMock(router)
    tokens = client._tokens
    for _ in range(BUDGET):
        tokens.invalidate("om", await tokens.get("om"))  # force a fresh mint every time
        clock.advance(10)
    with pytest.raises(ZohoAPIError, match="token_rate_limited"):
        await tokens.get("om")
    assert token.calls == BUDGET


async def test_budget_is_rolling_and_per_org(client, router, monkeypatch):
    clock = FakeClock(monkeypatch)
    token = TokenMock(router)
    tokens = client._tokens
    for _ in range(BUDGET):
        tokens.invalidate("om", await tokens.get("om"))
        clock.advance(100)  # mints at t = 0, 100, ..., 400
    clock.advance(50)  # t = 450: window still holds 5 mints
    with pytest.raises(ZohoAPIError, match="token_rate_limited"):
        await tokens.get("om")
    assert await tokens.get("ado")  # other org has its own budget
    clock.advance(WINDOW - 450 + 1)  # t = 601: the t = 0 mint left the window
    assert await tokens.get("om")
    assert token.calls == BUDGET + 2


async def test_failed_mints_count_against_the_budget(client, router, monkeypatch):
    clock = FakeClock(monkeypatch)
    route = router.post(TOKEN_URL).mock(return_value=httpx.Response(500, text="boom"))
    for _ in range(BUDGET):
        with pytest.raises(ZohoAPIError):
            await client._tokens.get("om")
        clock.advance(61)  # past the failure cooldown, inside the budget window
    with pytest.raises(ZohoAPIError, match="token_rate_limited"):
        await client._tokens.get("om")
    assert route.call_count == BUDGET


# ---- token-aware confirmation (Codex round 3 interleaving) -------------------

async def test_delayed_old_token_success_does_not_clear_the_replacements_failure_state(
        client, router):
    token = TokenMock(router)
    release_t1, release_t2 = asyncio.Event(), asyncio.Event()
    seen = {"t1": 0, "t2": 0}
    both_held, t2_started = asyncio.Event(), asyncio.Event()

    async def handler(request):
        used = request.headers["authorization"].rsplit(" ", 1)[1]
        if used == "AT-om-1":
            seen["t1"] += 1
            if seen["t1"] <= 2:  # A and B: successes held in flight
                if seen["t1"] == 2:
                    both_held.set()
                await release_t1.wait()
                return httpx.Response(200, json=OK)
            return invalid_token()  # C: T1 is rejected, which mints T2
        seen["t2"] += 1
        if seen["t2"] == 1:  # C's retry with T2: held, then rejected
            t2_started.set()
            await release_t2.wait()
        return invalid_token()

    router.get(LEAD, params=LEAD_PARAMS).mock(side_effect=handler)
    a = asyncio.create_task(get_lead(client))
    b = asyncio.create_task(get_lead(client))
    await both_held.wait()
    c = asyncio.create_task(get_lead(client))
    await t2_started.wait()
    release_t1.set()  # old T1 successes arrive after T2 exists
    assert await a == OK and await b == OK
    with pytest.raises(ZohoAPIError):  # D: T2 is rejected -> cooldown, no third mint
        await get_lead(client)
    release_t2.set()
    with pytest.raises(ZohoAPIError):
        await c
    for _ in range(5):
        with pytest.raises(ZohoAPIError):
            await get_lead(client)
    assert token.calls == 2


async def test_confirmation_for_a_replaced_token_is_ignored(client):
    tokens = client._tokens
    tokens._pending["om"] = 1
    tokens._replacement["om"] = "AT-current"
    tokens._cache["om"] = ("AT-current", 10 ** 12)
    tokens.confirmed("om", "AT-old")
    assert tokens._replacement.get("om") == "AT-current" and tokens._pending.get("om") == 1
    tokens.confirmed("om", "AT-current")
    assert "om" not in tokens._replacement and "om" not in tokens._pending


# ---- no sequence of calls can exceed the budget ----------------------------------

def _data_response(rng: random.Random) -> httpx.Response:
    roll = rng.random()
    if roll < 0.4:
        return httpx.Response(200, json=OK)
    if roll < 0.8:
        return invalid_token()
    if roll < 0.9:
        return httpx.Response(401, json={"code": "OAUTH_SCOPE_MISMATCH"})
    return httpx.Response(500, json={"code": "INTERNAL_ERROR"})


def _install(router, rng: random.Random):
    mints = []

    async def token_handler(request):
        await asyncio.sleep(0)
        mints.append(1)
        if rng.random() < 0.2:
            return httpx.Response(400, json={"error": "invalid_client"})
        return httpx.Response(200, json={"access_token": f"AT-om-{len(mints)}",
                                         "expires_in": 301})  # 1 s cache, many expiries

    async def data_handler(request):
        await asyncio.sleep(rng.choice([0, 0, 0.001]))
        return _data_response(rng)

    router.post(TOKEN_URL).mock(side_effect=token_handler)
    router.get(LEAD, params=LEAD_PARAMS).mock(side_effect=data_handler)
    return mints


@pytest.mark.parametrize("seed", range(25))
async def test_no_sequential_call_pattern_exceeds_the_mint_budget(seed, router, monkeypatch):
    rng = random.Random(seed)
    clock = FakeClock(monkeypatch)
    mints = _install(router, rng)
    client = ZohoClient(ZohoConfig.from_env(ENV_BOTH))
    for _ in range(60):
        try:
            await get_lead(client)
        except ZohoAPIError:
            pass
        clock.advance(rng.uniform(0, 3))  # total simulated time stays far below 10 minutes
    assert len(mints) <= BUDGET


@pytest.mark.parametrize("seed", range(25))
async def test_no_concurrent_call_pattern_exceeds_the_mint_budget(seed, router, monkeypatch):
    rng = random.Random(1000 + seed)
    clock = FakeClock(monkeypatch)
    mints = _install(router, rng)
    client = ZohoClient(ZohoConfig.from_env(ENV_BOTH))
    for _ in range(12):
        await asyncio.gather(*[get_lead(client) for _ in range(rng.randint(2, 11))],
                             return_exceptions=True)
        clock.advance(rng.uniform(0, 3))
    assert len(mints) <= BUDGET
