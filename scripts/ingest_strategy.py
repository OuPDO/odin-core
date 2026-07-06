"""Strategy-SSoT JSONL-Ingest (Plan B, 2026-07-02).

Fuettert das obladen-strategy-brain-Export (`ssot/build/strategy_knowledge.jsonl`)
in die per-Org Qdrant-Collections (om/ado/do_knowledge), damit der Strategie-SSoT
im Telegram-Orakel lebt. Ergaenzt den md-Filesystem-Ingest (`scripts/ingest.py`)
um einen JSONL-Pfad mit explizitem Per-Record-Routing.

Routing (Entscheidung 2026-07-02 "Nach Ziel-Site"):
- portfolio + positionierung: nach `payload.entitaet` (OM/ADO/DO).
- ankunftspunkt (`entitaet=null`): nach Ziel-Site aus dem Text
  (davidobladen.de -> do, ado-site -> ado, om-site & landing:* -> om).

Idempotent: stabile Logical-ID -> deterministische UUID5; `content_hash`-Dedup
ueberspringt unveraenderte Records; Records die aus dem JSONL verschwinden werden
als Orphans aus der Collection geloescht (gefiltert ueber `payload.project`).

Run:  python -m scripts.ingest_strategy --file <path/to/strategy_knowledge.jsonl> [--dry-run]
"""
import argparse
import hashlib
import json
import logging
import os
import re
import sys
import time
import uuid

# odin-core-Root importierbar machen bei Direktaufruf (`python scripts/ingest_strategy.py`).
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config.embeddings import get_embeddings
from config.settings import settings
from knowledge.qdrant_store import (
    COLLECTIONS,
    delete_by_source_path,
    ensure_collection,
    get_client,
)
from qdrant_client.models import FieldCondition, Filter, MatchValue, PointStruct

logger = logging.getLogger("odin.ingest_strategy")

PROJECT = "obladen-strategy-brain"
SOURCE_TYPE = "strategy"
_ENTITAET_TO_ORG = {"OM": "om", "ADO": "ado", "DO": "do"}

_ANKER_RE = re.compile(r"^Ankunftspunkt (?P<target>.+?) für (?P<segment>[\w-]+):")
_ANGEBOT_RE = re.compile(r"Angebot: (?P<offering>[\w-]+)")


# --- Parsing / Routing (pure) ---
def parse_ankunftspunkt(text: str) -> tuple[str, str, str]:
    """Zieht (target, segment, offering) aus dem Ankunftspunkt-Text. Fail-fast."""
    m = _ANKER_RE.match(text)
    a = _ANGEBOT_RE.search(text)
    if not m or not a:
        raise ValueError(f"unparseable ankunftspunkt text: {text[:80]!r}")
    return m.group("target"), m.group("segment"), a.group("offering")


def target_to_org(target: str) -> str:
    """Ziel-Site -> Org: davidobladen.de->do, ado-site->ado, sonst (om-site, landing:*)->om."""
    if "davidobladen.de" in target:
        return "do"
    if target.startswith("ado-site"):
        return "ado"
    return "om"


def route_record(record: dict) -> str:
    """Ziel-Org (om/ado/do) fuer einen SSoT-Record. Fail-fast bei unroutbar."""
    payload = record.get("payload", {})
    entitaet = payload.get("entitaet")
    if entitaet in _ENTITAET_TO_ORG:
        return _ENTITAET_TO_ORG[entitaet]
    if payload.get("kind") == "ankunftspunkt":
        target, _, _ = parse_ankunftspunkt(record["text"])
        return target_to_org(target)
    raise ValueError(
        f"cannot route record (entitaet={entitaet!r}, kind={payload.get('kind')!r})")


def logical_id(record: dict) -> str:
    """Stabile, content-unabhaengige Logical-ID -> source_path + Punkt-ID."""
    p = record.get("payload", {})
    kind = p.get("kind")
    if kind == "portfolio":
        return f"portfolio/{p['ref']}"
    if kind == "positionierung":
        return f"positionierung/{p['entitaet']}/{p['scope']}"
    if kind == "ankunftspunkt":
        target, segment, offering = parse_ankunftspunkt(record["text"])
        return f"ankunftspunkt/{target}/{segment}/{offering}"
    raise ValueError(f"unknown kind for logical_id: {kind!r}")


def content_hash(text: str) -> str:
    """sha256 des Record-Texts -- Aenderungssignal fuer inkrementellen Re-Ingest."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def point_id(source_path: str) -> str:
    """Deterministische UUID5 (mirror scripts/ingest.py; 1 Record = 1 Chunk -> ::0)."""
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"{PROJECT}/{source_path}::0"))


def build_payload(record: dict, org: str, source_path: str) -> dict:
    """ODIN-Payload-Kontrakt + Strategie-Meta verbatim (fuer Filterung im Orakel)."""
    text = record["text"]
    payload = {
        **dict(record.get("payload", {})),  # typ, kind, entitaet, ref/segment/scope
        "org": org,
        "project": PROJECT,
        "source_path": source_path,
        "source_type": SOURCE_TYPE,
        "git_remote": None,
        "content_hash": content_hash(text),
        "chunk_text": text,
    }
    if payload.get("kind") == "ankunftspunkt":
        target, _, offering = parse_ankunftspunkt(text)
        payload["target"] = target
        payload["offering"] = offering
    return payload


def build_points(records: list[dict]) -> dict[str, list[dict]]:
    """Routed Records nach org gruppiert. Eintrag: {id, source_path, text, payload}."""
    by_org: dict[str, list[dict]] = {org: [] for org in COLLECTIONS}
    seen: set[str] = set()
    for rec in records:
        org = route_record(rec)
        sp = logical_id(rec)
        pid = point_id(sp)
        if pid in seen:
            raise ValueError(f"duplicate logical id: {sp}")
        seen.add(pid)
        by_org[org].append({
            "id": pid,
            "source_path": sp,
            "text": rec["text"],
            "payload": build_payload(rec, org, sp),
        })
    return by_org


# --- I/O ---
def load_jsonl(path: str) -> list[dict]:
    records: list[dict] = []
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                records.append(json.loads(line))
    return records


def _embed_with_retry(emb, texts: list[str], max_attempts: int = 3) -> list:
    """Embed mit Retry + exponentiellem Backoff (mirror scripts/ingest.py)."""
    last_exc: Exception | None = None
    for attempt in range(max_attempts):
        try:
            return emb.embed_documents(texts)
        except Exception as exc:
            last_exc = exc
            logger.warning("embed_documents attempt %d failed: %s", attempt + 1, exc)
            time.sleep(2 ** attempt)
    raise last_exc  # type: ignore[misc]


def _existing(client, org: str, page: int = 256) -> dict[str, str]:
    """source_path -> content_hash aller bereits fuer PROJECT indexierten *Strategy*-Points.

    Scope MUSS project UND source_type="strategy" sein: der generische Repo-Ingest
    (scripts/ingest.py) vergibt denselben project-Wert fuer die .md-Dateien des
    obladen-strategy-brain-Repos (org "unknown" -> do-Fallback). Ohne den source_type-
    Filter wuerde der Orphan-Prune diese fremden Repo-Points faelschlich loeschen.
    """
    out: dict[str, str] = {}
    flt = Filter(must=[
        FieldCondition(key="project", match=MatchValue(value=PROJECT)),
        FieldCondition(key="source_type", match=MatchValue(value=SOURCE_TYPE)),
    ])
    offset = None
    while True:
        points, offset = client.scroll(
            collection_name=COLLECTIONS[org],
            scroll_filter=flt,
            with_payload=["source_path", "content_hash"],
            with_vectors=False,
            limit=page,
            offset=offset,
        )
        for p in points:
            pl = p.payload or {}
            sp = pl.get("source_path")
            if sp:
                out[sp] = pl.get("content_hash") or ""
        if offset is None:
            break
    return out


def ingest(records: list[dict], *, dry_run: bool = False) -> dict:
    """Ingestiert routed Records in die per-Org Collections. dry_run = nur Routing + Summary."""
    by_org = build_points(records)
    summary = {
        "routed": {org: len(items) for org, items in by_org.items()},
        "embedded": 0,
        "skipped": 0,
        "deleted": 0,
        "dry_run": dry_run,
    }
    if dry_run:
        for org, items in by_org.items():
            for it in items:
                logger.info("[dry-run] %s <- %s", COLLECTIONS[org], it["source_path"])
        return summary

    emb = get_embeddings()
    client = get_client()
    for org in COLLECTIONS:
        ensure_collection(client, org, settings.azure_embedding_dim)

    for org, items in by_org.items():
        existing = _existing(client, org)
        seen: set[str] = set()
        to_embed: list[dict] = []
        for it in items:
            seen.add(it["source_path"])
            if existing.get(it["source_path"]) == it["payload"]["content_hash"]:
                summary["skipped"] += 1
                continue
            to_embed.append(it)

        if to_embed:
            vectors = _embed_with_retry(emb, [it["text"] for it in to_embed])
            points = [
                PointStruct(id=it["id"], vector=v, payload=it["payload"])
                for it, v in zip(to_embed, vectors)
            ]
            client.upsert(collection_name=COLLECTIONS[org], points=points)
            summary["embedded"] += len(points)

        # Orphans: fuer PROJECT indexiert, aber nicht mehr im JSONL -> loeschen.
        for sp in set(existing) - seen:
            delete_by_source_path(client, org, sp)
            summary["deleted"] += 1

        logger.info("%s: embedded=%d skipped=%d deleted=%d",
                    COLLECTIONS[org], summary["embedded"], summary["skipped"], summary["deleted"])
    return summary


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    p = argparse.ArgumentParser(
        description="Ingest obladen-strategy-brain SSoT JSONL in die per-Org Qdrant-Collections.")
    p.add_argument("--file", required=True, help="Pfad zu strategy_knowledge.jsonl")
    p.add_argument("--dry-run", action="store_true",
                   help="Nur Routing + Summary, keine Embeddings/Qdrant-Writes")
    args = p.parse_args()
    records = load_jsonl(args.file)
    summary = ingest(records, dry_run=args.dry_run)
    print(json.dumps(summary, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
