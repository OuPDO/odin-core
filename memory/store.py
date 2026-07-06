"""Lean-Core Memory-Store: bidirektionale Wissens-Schicht (Postgres autoritativ,
Qdrant rebuildbarer Index). Deterministische key-basierte Reconciliation.

Postgres wird immer zuerst geschrieben; Qdrant ist best-effort (Fehler = Warn-Log,
reindex_memory heilt). Harte Loeschung findet nie statt -- Invalidierung setzt valid_to.
"""
import hashlib
import logging
from datetime import datetime, timezone

from config.embeddings import get_embeddings
from config.settings import settings
from knowledge.qdrant_store import (
    MEMORY_COLLECTION,
    delete_point,
    ensure_memory_collection,
    get_client,
    search_memory_points,
    upsert_memory_point,
)
from memory.postgres import get_supabase

logger = logging.getLogger("odin.memory.store")

TABLE = "odin_memory"
VALID_KINDS = {"semantic", "episodic", "procedural"}


def _hash(content: str) -> str:
    """sha256 des getrimmten Inhalts -- Idempotenz-/Sync-Signal."""
    return hashlib.sha256(content.strip().encode("utf-8")).hexdigest()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _current_by_key(db, subject: str | None, key: str, org: str | None) -> dict | None:
    """Aktuell gueltige Zeile fuer (subject, key, org) oder None."""
    q = db.table(TABLE).select("*").eq("key", key).is_("valid_to", "null")
    if subject is not None:
        q = q.eq("subject", subject)
    if org is not None:
        q = q.eq("org", org)
    rows = q.limit(1).execute().data
    return rows[0] if rows else None


def _current_by_hash(db, subject: str | None, content_hash: str) -> dict | None:
    """Aktuell gueltige Zeile mit gleichem content_hash (Append-Dedup) oder None."""
    q = db.table(TABLE).select("*").eq("content_hash", content_hash).is_("valid_to", "null")
    if subject is not None:
        q = q.eq("subject", subject)
    rows = q.limit(1).execute().data
    return rows[0] if rows else None


def _index(memory_id: str, content: str, kind: str, subject: str | None, key: str | None,
           org: str | None, provenance: dict | None, content_hash: str,
           valid_from: str | None) -> None:
    """Best-effort: Embed + Qdrant-Point-Upsert. Fehler = Warn-Log (reindex_memory heilt)."""
    try:
        vec = get_embeddings().embed_query(content)
        client = get_client()
        ensure_memory_collection(client, settings.azure_embedding_dim)
        payload = {
            "memory_id": memory_id,
            "kind": kind,
            "subject": subject,
            "key": key,
            "org": org,
            "content": content,
            "source": (provenance or {}).get("source"),
            "valid_from": valid_from,
            "content_hash": content_hash,
        }
        upsert_memory_point(client, memory_id, vec, payload)
    except Exception as exc:
        logger.warning("Qdrant-Index fehlgeschlagen fuer memory %s: %s (reindex_memory heilt)",
                       memory_id, exc)


def _drop_point(memory_id: str) -> None:
    """Best-effort: Qdrant-Point loeschen."""
    try:
        delete_point(get_client(), MEMORY_COLLECTION, memory_id)
    except Exception as exc:
        logger.warning("Qdrant-Delete fehlgeschlagen fuer memory %s: %s", memory_id, exc)


def _insert(db, content: str, kind: str, subject: str | None, key: str | None,
            org: str | None, provenance: dict | None, content_hash: str,
            valid_from: str | None) -> str:
    """Fuegt eine neue Zeile ein (Postgres zuerst), indexiert dann. Gibt neue id zurueck."""
    row = {
        "kind": kind,
        "content": content,
        "subject": subject,
        "key": key,
        "org": org,
        "provenance": provenance,
        "content_hash": content_hash,
    }
    if valid_from:
        row["valid_from"] = valid_from
    inserted = db.table(TABLE).insert(row).execute().data[0]
    memory_id = inserted["id"]
    _index(memory_id, content, kind, subject, key, org, provenance, content_hash,
           inserted.get("valid_from"))
    return memory_id


def _supersede(db, old: dict, content: str, kind: str, subject: str | None, key: str | None,
               org: str | None, provenance: dict | None, content_hash: str,
               valid_from: str | None) -> str:
    """Atomarer Supersede via Postgres-RPC (invalidate-alt + insert-neu + verketten in EINER
    Transaktion; DB-Backstop = UNIQUE-Partial-Index). Danach Qdrant best-effort:
    neuen Point indexieren, alten loeschen."""
    data = db.rpc("odin_memory_supersede", {
        "p_old_id": old["id"],
        "p_kind": kind,
        "p_content": content,
        "p_subject": subject,
        "p_key": key,
        "p_org": org,
        "p_provenance": provenance,
        "p_content_hash": content_hash,
        "p_valid_from": valid_from,
    }).execute().data
    new = data[0] if isinstance(data, list) else data
    new_id = new["id"]
    _index(new_id, content, kind, subject, key, org, provenance, content_hash,
           new.get("valid_from"))
    _drop_point(old["id"])
    return new_id


def remember(content: str, kind: str = "semantic", subject: str | None = None,
             key: str | None = None, org: str | None = None,
             provenance: dict | None = None, valid_from: str | None = None) -> dict:
    """Persistiere einen Fakt/ein Erlebnis/eine Regel. key gesetzt = Upsert, key null = Append.

    Returns: {"id": <uuid>, "action": "insert"|"supersede"|"noop"}
    """
    if kind not in VALID_KINDS:
        raise ValueError(f"ungueltiges kind: {kind!r} (erlaubt: {sorted(VALID_KINDS)})")
    content_hash = _hash(content)
    db = get_supabase()

    if key is not None:
        existing = _current_by_key(db, subject, key, org)
        if existing is not None:
            if existing["content_hash"] == content_hash:
                return {"id": existing["id"], "action": "noop"}
            new_id = _supersede(db, existing, content, kind, subject, key, org,
                                provenance, content_hash, valid_from)
            return {"id": new_id, "action": "supersede"}
        new_id = _insert(db, content, kind, subject, key, org, provenance, content_hash, valid_from)
        return {"id": new_id, "action": "insert"}

    existing = _current_by_hash(db, subject, content_hash)
    if existing is not None:
        return {"id": existing["id"], "action": "noop"}
    new_id = _insert(db, content, kind, subject, key, org, provenance, content_hash, valid_from)
    return {"id": new_id, "action": "insert"}


def update_memory(new_content: str, id: str | None = None, subject: str | None = None,
                  key: str | None = None, org: str | None = None) -> dict:
    """Expliziter Supersede eines bestehenden Memories (per id ODER (subject,key,org)).

    Returns: {"id": <uuid>, "action": "supersede"|"noop"}
    """
    if id is None and key is None:
        raise ValueError("update_memory braucht id oder key")
    db = get_supabase()
    if id is not None:
        rows = db.table(TABLE).select("*").eq("id", id).limit(1).execute().data
        old = rows[0] if rows else None
    else:
        old = _current_by_key(db, subject, key, org)
    if old is None:
        raise ValueError("zu aktualisierendes Memory nicht gefunden")

    content_hash = _hash(new_content)
    if old["content_hash"] == content_hash:
        return {"id": old["id"], "action": "noop"}
    new_id = _supersede(db, old, new_content, old["kind"], old.get("subject"),
                        old.get("key"), old.get("org"), old.get("provenance"),
                        content_hash, None)
    return {"id": new_id, "action": "supersede"}


def invalidate(id: str) -> None:
    """Setzt valid_to (bi-temporal), loescht den Qdrant-Point. Zeile bleibt (Audit)."""
    db = get_supabase()
    db.table(TABLE).update({"valid_to": _now()}).eq("id", id).execute()
    _drop_point(id)


def recall_about(subject: str, kind: str | None = None) -> list[dict]:
    """Strukturiertes Recall: nur aktuell gueltige (valid_to IS NULL) Zeilen fuer subject."""
    q = get_supabase().table(TABLE).select("*").eq("subject", subject).is_("valid_to", "null")
    if kind is not None:
        q = q.eq("kind", kind)
    return q.execute().data


def search_memory(query: str, org: str | None = None) -> list:
    """Semantische Memory-Suche. Degradiert bei Qdrant/Azure-Fehler auf leere Liste."""
    try:
        vec = get_embeddings().embed_query(query)
        client = get_client()
        return search_memory_points(client, vec, org=org)
    except Exception as exc:
        logger.warning("search_memory fehlgeschlagen: %s", exc)
        return []
