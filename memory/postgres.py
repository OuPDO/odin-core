"""Postgres/Supabase Memory — Agent state, checkpoints, conversation history."""

import logging
from datetime import datetime, timezone

from supabase import create_client

from config import settings

logger = logging.getLogger("odin.memory.postgres")

_client = None


def get_supabase():
    """Get or create the Supabase client."""
    global _client
    if _client is None:
        _client = create_client(settings.supabase_url, settings.supabase_service_key)
    return _client


async def save_conversation(
    user_id: str,
    chat_id: str,
    message: str,
    response: str,
    org: str,
) -> None:
    """Save a conversation turn to Supabase."""
    try:
        client = get_supabase()
        client.table("conversations").insert({
            "user_id": user_id,
            "chat_id": chat_id,
            "message": message,
            "response": response,
            "org": org,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }).execute()
    except Exception:
        logger.exception("Fehler beim Speichern der Konversation")


async def get_recent_conversations(
    chat_id: str,
    limit: int = 10,
) -> list[dict]:
    """Get recent conversation history for a chat."""
    try:
        client = get_supabase()
        result = (
            client.table("conversations")
            .select("*")
            .eq("chat_id", chat_id)
            .order("created_at", desc=True)
            .limit(limit)
            .execute()
        )
        return list(reversed(result.data))
    except Exception:
        logger.exception("Fehler beim Laden der Konversationen")
        return []
