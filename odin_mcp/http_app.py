"""API-Key-Guard fuer den Remote-HTTP-MCP (SP-4.4).

ASGI-Wrapper vor `mcp.streamable_http_app()`. Verlangt einen gueltigen API-Key
pro HTTP-Request — als `Authorization: Bearer <key>` oder `X-API-Key: <key>`.
Fail-closed: ohne konfigurierten Key wird jeder Request abgelehnt.

Der Guard beruehrt nur HTTP-Scopes; lifespan/websocket laufen unveraendert
zum inneren App durch (sonst wuerde der Session-Manager nie initialisiert).
"""
import hmac

from starlette.responses import JSONResponse


class ApiKeyGuard:
    """Schuetzt eine ASGI-App mit einem statischen API-Key (Bearer oder X-API-Key)."""

    def __init__(self, app, api_key: str) -> None:
        self._app = app
        self._api_key = api_key or ""

    async def __call__(self, scope, receive, send) -> None:
        if scope.get("type") != "http":
            await self._app(scope, receive, send)
            return
        if self._authorized(scope):
            await self._app(scope, receive, send)
            return
        response = JSONResponse({"error": "unauthorized"}, status_code=401)
        await response(scope, receive, send)

    def _authorized(self, scope) -> bool:
        if not self._api_key:
            return False
        headers = {key.lower(): value for key, value in scope.get("headers", [])}
        provided = ""
        auth = headers.get(b"authorization", b"").decode("latin-1")
        if auth[:7].lower() == "bearer ":
            provided = auth[7:].strip()
        if not provided:
            provided = headers.get(b"x-api-key", b"").decode("latin-1").strip()
        if not provided:
            return False
        return hmac.compare_digest(provided, self._api_key)
