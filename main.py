import asyncio
import logging

from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, Response
from telegram import Update

from config import settings
from telegram_bot.bot import create_bot

logging.basicConfig(
    level=getattr(logging, settings.odin_log_level),
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
)
logger = logging.getLogger("odin")


@asynccontextmanager
async def lifespan(app: FastAPI):
    bot_app = create_bot()
    await bot_app.initialize()
    await bot_app.start()

    # Use polling mode — works reliably for single-user bot
    # Webhook mode can be enabled later for lower latency
    await bot_app.updater.start_polling(drop_pending_updates=True)
    logger.info("Telegram Polling gestartet.")

    app.state.bot_app = bot_app
    logger.info("ODIN gestartet. Umgebung: %s", settings.odin_environment)
    yield

    if bot_app.updater and bot_app.updater.running:
        await bot_app.updater.stop()
    await bot_app.stop()
    await bot_app.shutdown()
    logger.info("ODIN gestoppt.")


app = FastAPI(title="ODIN Core", version="0.1.0", lifespan=lifespan)


@app.get("/health")
async def health():
    return {"status": "ok", "service": "odin-core", "version": "0.1.0"}


@app.post("/webhook/telegram")
async def telegram_webhook(request: Request):
    """Telegram webhook endpoint for production mode."""
    bot_app = request.app.state.bot_app
    data = await request.json()
    update = Update.de_json(data, bot_app.bot)
    await bot_app.process_update(update)
    return Response(status_code=200)
