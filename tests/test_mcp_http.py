"""Tests fuer den API-Key-Guard des Remote-HTTP-MCP (SP-4.4).

Der Guard ist ein ASGI-Wrapper vor `mcp.streamable_http_app()`. Er schuetzt den
Endpoint mit einem API-Key (Bearer ODER X-API-Key). Diese Tests pruefen die
Security-Logik isoliert, ohne DB/Telegram/MCP-Handshake.
"""
import pytest

from odin_mcp.http_app import ApiKeyGuard

API_KEY = "s3cr3t-odin-mcp-key"


async def _ok_app(scope, receive, send):
    """Minimaler Inner-ASGI-Endpoint, der 200 zurueckgibt."""
    await send({"type": "http.response.start", "status": 200, "headers": []})
    await send({"type": "http.response.body", "body": b"ok"})


async def _run(guard, headers):
    """Ruft den Guard mit gegebenen Roh-Headers auf, liefert den HTTP-Status."""
    scope = {"type": "http", "method": "POST", "path": "/mcp", "headers": headers}

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    sent = []

    async def send(msg):
        sent.append(msg)

    await guard(scope, receive, send)
    return next(m["status"] for m in sent if m["type"] == "http.response.start")


@pytest.mark.asyncio
async def test_missing_credentials_rejected():
    guard = ApiKeyGuard(_ok_app, API_KEY)
    assert await _run(guard, headers=[]) == 401


@pytest.mark.asyncio
async def test_wrong_bearer_rejected():
    guard = ApiKeyGuard(_ok_app, API_KEY)
    headers = [(b"authorization", b"Bearer falsch")]
    assert await _run(guard, headers) == 401


@pytest.mark.asyncio
async def test_correct_bearer_passes_through():
    guard = ApiKeyGuard(_ok_app, API_KEY)
    headers = [(b"authorization", f"Bearer {API_KEY}".encode())]
    assert await _run(guard, headers) == 200


@pytest.mark.asyncio
async def test_bearer_scheme_case_insensitive():
    guard = ApiKeyGuard(_ok_app, API_KEY)
    headers = [(b"authorization", f"bearer {API_KEY}".encode())]
    assert await _run(guard, headers) == 200


@pytest.mark.asyncio
async def test_x_api_key_header_supported():
    guard = ApiKeyGuard(_ok_app, API_KEY)
    headers = [(b"x-api-key", API_KEY.encode())]
    assert await _run(guard, headers) == 200


@pytest.mark.asyncio
async def test_empty_configured_key_fails_closed():
    # Kein Key konfiguriert -> Endpoint bleibt zu, auch wenn Client leeren Wert schickt.
    guard = ApiKeyGuard(_ok_app, "")
    headers = [(b"authorization", b"Bearer ")]
    assert await _run(guard, headers) == 401


@pytest.mark.asyncio
async def test_non_http_scope_passes_through():
    # Lifespan/websocket-Scopes duerfen nicht vom Guard geblockt werden.
    calls = []

    async def inner(scope, receive, send):
        calls.append(scope["type"])

    guard = ApiKeyGuard(inner, API_KEY)
    await guard({"type": "lifespan"}, None, None)
    assert calls == ["lifespan"]
