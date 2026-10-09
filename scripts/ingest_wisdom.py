"""Wisdom-JSONL-Ingest (learn-from-millionaires -> wisdom_knowledge).

Liest `wisdom.jsonl` (Export des Projekts learn-from-millionaires, eine Zeile je
atomarer Insight) und schreibt sie in die eigene Qdrant-Collection `wisdom_knowledge`.
`text` wird eingebettet, der Rest ist Payload (theme, person, published, source_url ...).

Sicherheit: Default ist DRY-RUN. Geschrieben wird nur mit `--apply`.
Idempotent: stabile Point-ID (UUID5 aus der Insight-ID), `content_hash`-Dedup
ueberspringt unveraenderte Records, aus dem JSONL verschwundene Records werden als
Orphans geloescht (Scope: nur diese Collection).

Run:  python -m scripts.ingest_wisdom --file <wisdom.jsonl> [--apply]
"""
import argparse
import hashlib
import json
import logging
import os
import sys
import uuid

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from qdrant_client.models import PointStruct

from config.embeddings import get_embeddings
from config.settings import settings
from knowledge.qdrant_store import get_client
from knowledge.wisdom_store import (
    WISDOM_COLLECTION,
    delete_wisdom_by_source_path,
    ensure_wisdom_collection,
    existing_wisdom_hashes,
)
from scripts.ingest import _embed_batched, _upsert_with_retry

logger = logging.getLogger("odin.ingest_wisdom")

PROJECT = "learn-from-millionaires"
SOURCE_TYPE = "wisdom"
UPSERT_BATCH = 64
# Felder, die als Payload mitgehen (ausser `text`, das ist chunk_text).
PAYLOAD_FIELDS = (
    "lesson_en", "lesson_de", "answer_en", "answer_de", "theme", "person", "channel",
    "source_url", "confidence", "published", "start_seconds", "stance",
)
REQUIRED_FIELDS = ("id", "text", "source_url")


def load_jsonl(path: str) -> list[dict]:
    records: list[dict] = []
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                records.append(json.loads(line))
    return records


def validate_record(rec: dict) -> None:
    """Fail-fast: jede Insight braucht ID, Text und Quelle mit Zeitstempel."""
    missing = [f for f in REQUIRED_FIELDS if not rec.get(f)]
    if missing:
        raise ValueError(f"record {rec.get('id')!r} missing {missing}")
    if "&t=" not in rec["source_url"]:
        raise ValueError(f"record {rec['id']!r}: source_url ohne Zeitstempel")


def point_id(insight_id: str) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"{PROJECT}/{insight_id}::0"))


def content_hash(rec: dict) -> str:
    """Hash ueber Text UND Payload-Felder: aendert sich das Datum, wird neu geschrieben."""
    material = json.dumps({"text": rec["text"], **{f: rec.get(f) for f in PAYLOAD_FIELDS}},
                          sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def build_payload(rec: dict) -> dict:
    payload = {f: rec.get(f) for f in PAYLOAD_FIELDS}
    payload.update({
        "typ": "wisdom", "project": PROJECT, "source_type": SOURCE_TYPE,
        "source_path": rec["id"], "content_hash": content_hash(rec),
        "chunk_text": rec["text"], "org": "do", "git_remote": None,
    })
    return payload


def build_points(records: list[dict]) -> list[dict]:
    seen: set[str] = set()
    items = []
    for rec in records:
        validate_record(rec)
        pid = point_id(rec["id"])
        if pid in seen:
            raise ValueError(f"duplicate insight id: {rec['id']}")
        seen.add(pid)
        items.append({"id": pid, "source_path": rec["id"], "text": rec["text"],
                      "payload": build_payload(rec)})
    return items


def ingest(records: list[dict], *, apply: bool = False) -> dict:
    items = build_points(records)
    summary = {"records": len(items), "embedded": 0, "skipped": 0, "deleted": 0,
               "applied": apply, "collection": WISDOM_COLLECTION}
    if not apply:
        return summary

    emb = get_embeddings()
    client = get_client()
    ensure_wisdom_collection(client, settings.azure_embedding_dim)
    existing = existing_wisdom_hashes(client)
    to_embed = [it for it in items if existing.get(it["source_path"]) != it["payload"]["content_hash"]]
    summary["skipped"] = len(items) - len(to_embed)

    for i in range(0, len(to_embed), UPSERT_BATCH):
        batch = to_embed[i:i + UPSERT_BATCH]
        vectors = _embed_batched(emb, [it["text"] for it in batch])
        points = [PointStruct(id=it["id"], vector=v, payload=it["payload"]) for it, v in zip(batch, vectors)]
        _upsert_with_retry(client, WISDOM_COLLECTION, points)
        summary["embedded"] += len(points)

    seen = {it["source_path"] for it in items}
    for sp in set(existing) - seen:
        delete_wisdom_by_source_path(client, sp)
        summary["deleted"] += 1
    return summary


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    p = argparse.ArgumentParser(description="Ingest wisdom.jsonl in die Qdrant-Collection wisdom_knowledge.")
    p.add_argument("--file", required=True, help="Pfad zu wisdom.jsonl")
    p.add_argument("--apply", action="store_true",
                   help="Wirklich schreiben (Embeddings + Qdrant). Ohne Flag nur Validierung + Summary.")
    args = p.parse_args()
    print(json.dumps(ingest(load_jsonl(args.file), apply=args.apply), indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
