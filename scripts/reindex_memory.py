"""Baut memory_knowledge aus den aktuell gueltigen odin_memory-Zeilen neu.

Postgres autoritativ, Index rebuildbar. content_hash-Idempotenz analog SP-3.1:
Points mit unveraendertem Hash werden nicht neu embedded.

Aufruf:
    python -m scripts.reindex_memory
    python scripts/reindex_memory.py
"""
import logging
import os
import sys

# Macht den odin-core-Root importierbar bei Direktaufruf.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config.embeddings import get_embeddings
from config.settings import settings
from knowledge.qdrant_store import (
    MEMORY_COLLECTION,
    delete_point,
    ensure_memory_collection,
    existing_memory_hashes,
    get_client,
    upsert_memory_point,
)
from memory.postgres import get_supabase

logger = logging.getLogger("odin.reindex_memory")

TABLE = "odin_memory"


def valid_memories() -> list[dict]:
    """Alle aktuell gueltigen (valid_to IS NULL) Memory-Zeilen."""
    return get_supabase().table(TABLE).select("*").is_("valid_to", "null").execute().data


def reindex_memory() -> dict:
    """Rebuild der memory_knowledge-Collection. Gibt {valid, indexed, skipped, failed, pruned}."""
    rows = valid_memories()
    emb = get_embeddings()
    client = get_client()
    ensure_memory_collection(client, settings.azure_embedding_dim)
    existing = existing_memory_hashes(client)

    indexed = skipped = failed = pruned = 0
    for r in rows:
        mid = r["id"]
        if existing.get(mid) == r["content_hash"]:
            skipped += 1
            continue
        try:
            vec = emb.embed_query(r["content"])
            payload = {
                "memory_id": mid,
                "kind": r.get("kind"),
                "subject": r.get("subject"),
                "key": r.get("key"),
                "org": r.get("org"),
                "content": r["content"],
                "source": (r.get("provenance") or {}).get("source"),
                "valid_from": r.get("valid_from"),
                "content_hash": r["content_hash"],
            }
            upsert_memory_point(client, mid, vec, payload)
            indexed += 1
        except Exception as exc:
            logger.warning("reindex_memory: Point %s fehlgeschlagen: %s", mid, exc)
            failed += 1

    valid_ids = {r["id"] for r in rows}
    for pid in set(existing) - valid_ids:
        try:
            delete_point(client, MEMORY_COLLECTION, pid)
            pruned += 1
        except Exception as exc:
            logger.warning("reindex_memory: Stale point %s konnte nicht geloescht werden: %s", pid, exc)

    logger.info(
        "reindex_memory: valid=%d indexed=%d skipped=%d failed=%d pruned=%d",
        len(rows), indexed, skipped, failed, pruned,
    )
    return {"valid": len(rows), "indexed": indexed, "skipped": skipped, "failed": failed, "pruned": pruned}


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    result = reindex_memory()
    logger.info("reindex_memory summary: %s", result)


if __name__ == "__main__":
    main()
