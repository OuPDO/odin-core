"""Deadline, response size cap, malformed bodies, error-code allowlist and log hygiene."""
import asyncio
import logging
import os
import subprocess
import sys
import time
from pathlib import Path

import httpx
import pytest
import respx
from zoho_support import API, ENV_BOTH, ENV_OM, SECRETS, TOKEN_URL, TokenMock

import integrations.zoho.client as client_mod
from integrations.zoho import crm
from integrations.zoho.client import ZohoAPIError, ZohoClient, ZohoConfig

ROOT = Path(__file__).resolve().parent.parent
LEAD = f"{API}/crm/v6/Leads/123"
PARAMS = {"fields": "Full_Name"}
MAX_BYTES = 5 * 1024 * 1024
CHUNK = 1024 * 1024


@pytest.fixture
def router():
    with respx.mock(assert_all_called=False) as r:
        yield r


@pytest.fixture
def client():
    return ZohoClient(ZohoConfig.from_env(ENV_BOTH))


async def get_lead(client):
    return await client.get("om", "/crm/v6/Leads/123", PARAMS)


def mock_lead(router, **kwargs):
    return router.get(LEAD, params=PARAMS).mock(**kwargs)


# ---- overall deadline ------------------------------------------------------

def test_deadline_is_30_seconds():
    assert client_mod.OVERALL_TIMEOUT_SECONDS == 30


async def test_slow_data_response_hits_the_overall_deadline(client, router, monkeypatch):
    monkeypatch.setattr(client_mod, "OVERALL_TIMEOUT_SECONDS", 0.2)
    TokenMock(router)

    async def slow(request):
        await asyncio.sleep(5)
        return httpx.Response(200, json={})

    mock_lead(router, side_effect=slow)
    started = time.monotonic()
    with pytest.raises(ZohoAPIError, match="timed out"):
        await get_lead(client)
    assert time.monotonic() - started < 2


async def test_slow_token_mint_hits_the_overall_deadline(client, router, monkeypatch):
    monkeypatch.setattr(client_mod, "OVERALL_TIMEOUT_SECONDS", 0.2)
    TokenMock(router, delay=5)
    with pytest.raises(ZohoAPIError, match="timed out"):
        await get_lead(client)


# ---- response size cap -----------------------------------------------------

class Endless(httpx.AsyncByteStream):
    """Async body that never ends; counts the chunks the client actually pulled."""

    def __init__(self):
        self.chunks = 0

    async def __aiter__(self):
        while True:
            self.chunks += 1
            yield b"x" * CHUNK


async def test_endless_body_is_aborted_at_5_mb(client, router):
    TokenMock(router)
    body = Endless()
    mock_lead(router, return_value=httpx.Response(200, stream=body))
    with pytest.raises(ZohoAPIError, match="too large"):
        await get_lead(client)
    assert body.chunks <= 7


async def test_declared_oversize_is_rejected(client, router):
    TokenMock(router)
    mock_lead(router, return_value=httpx.Response(
        200, headers={"content-length": str(MAX_BYTES + 1)}, content=b"{}"))
    with pytest.raises(ZohoAPIError, match="too large"):
        await get_lead(client)


async def test_body_just_under_the_cap_is_accepted(client, router):
    TokenMock(router)
    head, tail = b'{"data":[{"id":"1","pad":"', b'"}]}'
    pad = b"x" * (MAX_BYTES - len(head) - len(tail))
    mock_lead(router, return_value=httpx.Response(200, content=head + pad + tail))
    out = await get_lead(client)
    assert out["data"][0]["id"] == "1"


async def test_oversized_error_body_is_also_capped(client, router):
    TokenMock(router)
    mock_lead(router, return_value=httpx.Response(500, stream=Endless()))
    with pytest.raises(ZohoAPIError, match="too large"):
        await get_lead(client)


# ---- malformed bodies ------------------------------------------------------

@pytest.mark.parametrize("content", [
    b"[]", b'"text"', b"null", b"123", b"true", b"", b"{", b"<html></html>",
    b"[" * 20000, b'{"a":' * 20000,
])
async def test_non_dict_or_broken_json_is_a_clean_error(content, client, router):
    TokenMock(router)
    mock_lead(router, return_value=httpx.Response(200, content=content))
    with pytest.raises(ZohoAPIError, match="unreadable"):
        await get_lead(client)


@pytest.mark.parametrize("content", [b"[]", b"null", b"not json", b'"AT-om-1"'])
async def test_non_dict_error_body_is_a_clean_error(content, client, router):
    TokenMock(router)
    mock_lead(router, return_value=httpx.Response(500, content=content))
    with pytest.raises(ZohoAPIError, match="HTTP 500"):
        await get_lead(client)


@pytest.mark.parametrize("payload", [
    [], "AT", None, {"access_token": 123}, {"access_token": ""}, {"access_token": {"a": 1}},
    {"access_token": None, "expires_in": 3600}, {},
])
async def test_malformed_token_response_is_a_clean_error(payload, client, router):
    router.post(TOKEN_URL).mock(return_value=httpx.Response(200, json=payload))
    with pytest.raises(ZohoAPIError):
        await get_lead(client)


async def test_unexpected_data_shape_is_a_clean_error(client, router):
    TokenMock(router)
    router.get(f"{API}/crm/v6/Leads", params={
        "fields": "Full_Name", "page": "1", "per_page": "50"}).mock(
        return_value=httpx.Response(200, json={"data": "oops"}))
    with pytest.raises(ZohoAPIError, match="unexpected"):
        await crm.list_records(client, "om", "Leads", ["Full_Name"])


# ---- error code allowlist --------------------------------------------------

TOKEN_SECRET = ENV_OM["ZOHO_OM_CLIENT_SECRET"]
REFRESH = ENV_OM["ZOHO_OM_REFRESH_TOKEN"]


@pytest.mark.parametrize("body", [
    {"error": TOKEN_SECRET}, {"error": REFRESH}, {"code": TOKEN_SECRET},
    {"error": "invalid_client", "message": TOKEN_SECRET},
    {"error": {"nested": REFRESH}}, {"error": [REFRESH]},
])
@pytest.mark.parametrize("status", [200, 400, 401])
async def test_token_endpoint_errors_never_echo_upstream_text(body, status, client, router,
                                                              caplog):
    caplog.set_level(logging.DEBUG)
    router.post(TOKEN_URL).mock(return_value=httpx.Response(status, json=body))
    with pytest.raises(ZohoAPIError) as exc:
        await get_lead(client)
    text = str(exc.value) + repr(exc.value) + caplog.text
    assert all(secret not in text for secret in SECRETS)


@pytest.mark.parametrize("body", [
    {"code": TOKEN_SECRET}, {"code": REFRESH}, {"error": TOKEN_SECRET},
    {"code": "INVALID_DATA", "message": REFRESH}, {"code": [REFRESH]}, {"code": {"a": REFRESH}},
])
@pytest.mark.parametrize("status", [400, 403, 429, 500])
async def test_data_errors_never_echo_upstream_text(body, status, client, router, caplog):
    caplog.set_level(logging.DEBUG)
    TokenMock(router)
    mock_lead(router, return_value=httpx.Response(status, json=body))
    with pytest.raises(ZohoAPIError) as exc:
        await get_lead(client)
    text = str(exc.value) + repr(exc.value) + caplog.text
    assert all(secret not in text for secret in SECRETS)
    assert str(status) in str(exc.value)


@pytest.mark.parametrize("code,shown", [
    ("INVALID_TOKEN", "INVALID_TOKEN"), ("OAUTH_SCOPE_MISMATCH", "OAUTH_SCOPE_MISMATCH"),
    ("NO_PERMISSION", "NO_PERMISSION"), ("INVALID_DATA", "INVALID_DATA"),
    ("REQUIRED_PARAM_MISSING", "REQUIRED_PARAM_MISSING"),
    ("INVALID_URL_PATTERN", "INVALID_URL_PATTERN"), ("LIMIT_REACHED", "LIMIT_REACHED"),
    (4, "4"), (57, "57"),
    ("SOMETHING_NEW", "upstream_error"), ("invalid_data", "upstream_error"),
    ("INVALID_DATA ", "upstream_error"), ("", "upstream_error"), (None, "upstream_error"),
    (True, "upstream_error"), (1.5, "upstream_error"), (10 ** 9, "upstream_error"),
    (-1, "upstream_error"),
])
async def test_only_allowlisted_codes_are_passed_on(code, shown, client, router):
    TokenMock(router)
    mock_lead(router, return_value=httpx.Response(400, json={"code": code}))
    with pytest.raises(ZohoAPIError) as exc:
        await get_lead(client)
    assert str(exc.value) == f"Zoho API returned HTTP 400, {shown}"


@pytest.mark.parametrize("code", ["invalid_client", "invalid_code", "access_denied"])
async def test_token_error_codes_on_the_allowlist_are_passed_on(code, client, router):
    router.post(TOKEN_URL).mock(return_value=httpx.Response(400, json={"error": code}))
    with pytest.raises(ZohoAPIError, match=code):
        await get_lead(client)


# ---- PII in logs -----------------------------------------------------------

EMAIL = "max@example.com"


@pytest.mark.parametrize("level", [logging.INFO, logging.DEBUG])
async def test_search_values_never_reach_the_logs(level, client, router, caplog):
    caplog.set_level(level)
    TokenMock(router)
    router.get(f"{API}/crm/v6/Contacts/search", params={
        "email": EMAIL, "page": "1", "per_page": "50"}).mock(
        return_value=httpx.Response(200, json={"data": []}))
    await crm.search(client, "om", "Contacts", "email", EMAIL)
    assert caplog.records == [] or all(
        EMAIL not in r.getMessage() and "max%40example.com" not in r.getMessage()
        for r in caplog.records)
    assert EMAIL not in caplog.text and "max%40example.com" not in caplog.text
    assert not [r for r in caplog.records if r.name.split(".")[0] in ("httpx", "httpcore")]


def test_creating_a_client_silences_http_loggers():
    for name in ("httpx", "httpcore"):
        logging.getLogger(name).setLevel(logging.NOTSET)
    ZohoClient(ZohoConfig.from_env(ENV_OM))
    for name in ("httpx", "httpcore"):
        assert logging.getLogger(name).level == logging.WARNING


def test_importing_the_client_silences_http_loggers():
    code = ("import logging, integrations.zoho.client; "
            "print(logging.getLogger('httpx').level, logging.getLogger('httpcore').level)")
    env = {**os.environ, "PYTHONPATH": str(ROOT)}
    proc = subprocess.run([sys.executable, "-c", code], cwd=ROOT, env=env, check=True,
                          capture_output=True, text=True)
    assert proc.stdout.split() == [str(logging.WARNING)] * 2
