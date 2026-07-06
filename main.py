import logging

from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, Response

from config import settings
from odin_mcp.http_app import ApiKeyGuard
from odin_mcp.server import mcp

logging.basicConfig(
    level=getattr(logging, settings.odin_log_level),
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
)
logger = logging.getLogger("odin")

# Sub-App einmal beim Import bauen -> danach existiert mcp.session_manager,
# das in der Host-Lifespan gestartet werden MUSS (sonst 500 "task group not
# initialized"). Der Guard schuetzt den Endpoint mit einem API-Key.
_mcp_asgi = mcp.streamable_http_app()
_mcp_guarded = ApiKeyGuard(_mcp_asgi, settings.odin_mcp_api_key)


async def _start_telegram(app: FastAPI):
    """Startet den odin-core-eigenen Telegram-Bot (nur wenn explizit aktiviert)."""
    from telegram_bot.bot import create_bot

    bot_app = create_bot()
    await bot_app.initialize()
    await bot_app.start()
    await bot_app.updater.start_polling(drop_pending_updates=True)
    app.state.bot_app = bot_app
    logger.info("Telegram Polling gestartet (odin-core-eigener Bot).")
    return bot_app


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.bot_app = None
    async with mcp.session_manager.run():
        logger.info("MCP-Server odin-knowledge bereit unter /mcp (HTTP, API-Key).")
        bot_app = None
        if settings.odin_telegram_enabled:
            bot_app = await _start_telegram(app)
        else:
            logger.info("Telegram im odin-core deaktiviert (Hermes besitzt @do_odin_bot).")

        logger.info("ODIN gestartet. Umgebung: %s", settings.odin_environment)
        yield

        if bot_app is not None:
            if bot_app.updater and bot_app.updater.running:
                await bot_app.updater.stop()
            await bot_app.stop()
            await bot_app.shutdown()
    logger.info("ODIN gestoppt.")


app = FastAPI(title="ODIN Core", version="0.1.0", lifespan=lifespan)

# Remote-HTTP-MCP: https://<host>/mcp (streamable-http + API-Key-Guard).
app.mount("/mcp", _mcp_guarded)


@app.get("/health")
async def health():
    return {"status": "ok", "service": "odin-core", "version": "0.1.0"}


@app.post("/webhook/telegram")
async def telegram_webhook(request: Request):
    """Telegram-Webhook — nur aktiv, wenn der odin-core-Bot laeuft."""
    from telegram import Update

    bot_app = getattr(request.app.state, "bot_app", None)
    if bot_app is None:
        return Response(status_code=404)
    data = await request.json()
    update = Update.de_json(data, bot_app.bot)
    await bot_app.process_update(update)
    return Response(status_code=200)
