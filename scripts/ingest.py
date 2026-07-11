import argparse
import hashlib
import json
import logging
from dataclasses import dataclass
import os
import re
import subprocess
import time
import uuid
from datetime import datetime, timezone

# Make the odin-core root importable when run directly (`python scripts/ingest.py`):
# direct execution puts scripts/ on sys.path, not the package root.
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config.embeddings import get_embeddings
from config.settings import settings
from knowledge.qdrant_store import (
    COLLECTIONS,
    delete_by_source_path,
    ensure_collection,
    existing_hashes,
    get_client,
)
from memory.registry import upsert_project
from qdrant_client.models import PointStruct

logger = logging.getLogger("odin.ingest")


@dataclass(frozen=True)
class IngestResult:
    """Ergebnis eines Ingest-Laufs -- traegt das Observability-Signal.

    embedded: neu eingebettete Chunks.
    skipped_unchanged: per content_hash uebersprungen (kein Azure-Call).
    errors: Quellen, die trotz aller Retries fehlschlugen (typisch: 429).
    """

    embedded: int
    skipped_unchanged: int
    errors: int


SKIP_DIRS = {"node_modules", ".git", ".venv", "venv", "dist", ".next", "__pycache__"}

SOURCE_GLOBS = {"README.md": "readme", "CLAUDE.md": "claude_md"}


def _source_type(rel: str) -> str:
    """Derive source_type from relative path."""
    low = rel.lower()
    base = os.path.basename(low)
    if base == "readme.md":
        return "readme"
    if base == "claude.md":
        return "claude_md"
    if low.startswith("docs/") or "/docs/" in low:
        return "doc"
    if low.startswith("wiki/") or "/wiki/" in low:
        return "wiki"
    if low.startswith("outputs/") or "/outputs/" in low:
        return "output"
    return "note"


def chunk_text(text: str, size: int = 800, overlap: int = 100) -> list[str]:
    """Teilt text in ueberlappende Chunks der Laenge size."""
    if overlap >= size:
        raise ValueError(f"overlap ({overlap}) must be < size ({size})")
    out, i = [], 0
    while i < len(text):
        out.append(text[i:i + size])
        i += size - overlap
    return [c for c in out if c.strip()]


def _file_hash(text: str) -> str:
    """sha256 des rohen File-Texts -- Aenderungssignal fuer inkrementellen Reindex."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def collect_sources(project_path: str) -> list[tuple[str, str]]:
    """Sammelt alle Markdown-Quellen eines Projekts mit source_type."""
    srcs: list[tuple[str, str]] = []
    for base, dirs, files in os.walk(project_path):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS and d != ".git"]
        for f in files:
            if f.endswith(".md"):
                fp = os.path.join(base, f)
                rel = os.path.relpath(fp, project_path)
                srcs.append((_source_type(rel), fp))
    return srcs


def _embed_with_retry(emb, chunks: list[str]) -> list:
    """Embed mit geduldigen Retries. Backoff (15/30/60/120s, cap 120) matcht das
    Azure-429-Fenster (60s) -- die alten 1/2/4s gaben vor dem Ratelimit-Reset auf.
    """
    last_exc: Exception | None = None
    attempts = settings.embed_max_attempts
    for attempt in range(attempts):
        try:
            return emb.embed_documents(chunks)
        except Exception as exc:
            last_exc = exc
            if attempt == attempts - 1:
                logger.warning("embed_documents attempt %d/%d failed: %s -- giving up",
                               attempt + 1, attempts, exc)
                break  # kein Sleep nach dem letzten Versuch
            wait = min(settings.embed_retry_base_seconds * (2 ** attempt), 120.0)
            logger.warning("embed_documents attempt %d/%d failed: %s -- sleeping %.0fs",
                           attempt + 1, attempts, exc, wait)
            time.sleep(wait)
    raise last_exc  # type: ignore[misc]


def ingest_embeddings(rows: list[dict]) -> IngestResult:
    """Inkrementeller Ingest: nur geaenderte/neue Dateien werden embedded (SP-3.1).

    Pro Repo werden die bereits indexierten content_hashes aus Qdrant gelesen und
    gegen die frisch gehashten Dateien verglichen. Unveraenderte Dateien werden
    uebersprungen (kein Azure-Call), geaenderte erst geloescht und neu embedded,
    on-disk verschwundene Dateien (Orphans) geloescht.

    Eine Datei wird atomar embedded (alle Chunks oder keiner) -- der content_hash
    wird erst nach erfolgreichem Upsert gesetzt. Scheitert das Embedden trotz aller
    Retries (typisch: anhaltendes 429), behaelt die Datei keinen Hash und wird im
    naechsten Lauf erneut versucht.

    Returns: IngestResult (embedded/skipped/errors).
    """
    emb = get_embeddings()
    client = get_client()
    for org in COLLECTIONS:
        ensure_collection(client, org, settings.azure_embedding_dim)
    total = skipped_total = errors_total = 0
    for r in rows:
        org = r["org"] if r["org"] in COLLECTIONS else "do"
        git_remote = r.get("git_remote")
        fs = r.get("fs_path") or r["path"]

        # Bereits indexierte Hashes (Quelle der Wahrheit = Qdrant). Ohne git_remote
        # (lokaler scan_projects-Pfad) oder bei Scroll-Fehler -> Full-Embed dieses Repos.
        if git_remote:
            try:
                existing = existing_hashes(client, org, git_remote)
            except Exception as exc:
                logger.warning("existing_hashes failed for %s: %s", git_remote, exc)
                existing = {}
        else:
            existing = {}

        seen: set[str] = set()
        embedded = skipped = deleted = errors = 0

        for stype, fp in collect_sources(fs):
            rel = os.path.relpath(fp, fs)
            logical = f"{git_remote}/{rel}" if git_remote else fp
            seen.add(logical)
            try:
                with open(fp, encoding="utf-8", errors="ignore") as fh:
                    text = fh.read()
                h = _file_hash(text)
                if existing.get(logical) == h:
                    skipped += 1
                    continue
                chunks = chunk_text(text)
                if not chunks:
                    if logical in existing:
                        delete_by_source_path(client, org, logical)
                        deleted += 1
                    continue
                if logical in existing:
                    delete_by_source_path(client, org, logical)  # geaendert -> alte Points weg
                vectors = _embed_with_retry(emb, chunks)
                points = [
                    PointStruct(
                        id=str(uuid.uuid5(uuid.NAMESPACE_URL, f"{logical}::{i}")),
                        vector=v,
                        payload={
                            "org": org,
                            "project": r["name"],
                            "source_path": logical,
                            "source_type": stype,
                            "git_remote": git_remote,
                            "content_hash": h,
                            "chunk_text": c,
                        },
                    )
                    for i, (c, v) in enumerate(zip(chunks, vectors))
                ]
                client.upsert(collection_name=COLLECTIONS[org], points=points)
                total += len(points)
                embedded += len(points)
            except Exception as exc:
                # Nach allen Retries gescheitert (typisch: anhaltendes 429). Quelle
                # bleibt ohne Hash -> naechster Lauf versucht sie erneut.
                logger.warning("Skipping source %s: %s", fp, exc)
                errors += 1
                continue

        # Orphans: waren indexiert, aber nicht mehr on-disk -> loeschen. Ein
        # fehlgeschlagenes Delete zaehlt als error, damit der Heartbeat rot wird
        # und der Orphan nicht still in Qdrant zurueckbleibt.
        for logical in set(existing.keys()) - seen:
            try:
                delete_by_source_path(client, org, logical)
                deleted += 1
            except Exception as exc:
                logger.warning("orphan delete failed for %s: %s", logical, exc)
                errors += 1

        skipped_total += skipped
        errors_total += errors
        if git_remote:
            logger.info("repo %s: embedded=%d skipped=%d deleted=%d errors=%d",
                        git_remote, embedded, skipped, deleted, errors)

    return IngestResult(embedded=total, skipped_unchanged=skipped_total,
                        errors=errors_total)


def _stack(path: str) -> str | None:
    pkg = os.path.join(path, "package.json")
    if os.path.isfile(pkg):
        try:
            with open(pkg) as f:
                deps = json.load(f).get("dependencies", {})
        except Exception:
            deps = {}
        if "next" in deps:
            return "Next.js"
        if "astro" in deps:
            return "Astro"
        return "Node"
    if os.path.isfile(os.path.join(path, "composer.json")):
        return "Laravel"
    if os.path.isfile(os.path.join(path, "pyproject.toml")) or os.path.isfile(os.path.join(path, "requirements.txt")):
        return "Python"
    return None

def _purpose(path: str) -> str | None:
    readme = os.path.join(path, "README.md")
    if os.path.isfile(readme):
        with open(readme, encoding="utf-8", errors="ignore") as fh:
            for line in fh:
                t = line.strip().lstrip("# ").strip()
                if t:
                    return t[:200]
    return None

def _canonical_remote(url: str | None) -> str | None:
    """Strip credentials and reduce any remote URL to canonical owner/repo."""
    if not url:
        return None
    u = url.strip()
    # ssh: git@github.com:owner/repo(.git)
    m = re.match(r"git@[^:]+:(.+?)(?:\.git)?/?$", u)
    if m:
        return m.group(1)
    # https (with optional creds): https://[creds@]host/owner/repo(.git)
    m = re.match(r"https?://(?:[^@/]+@)?[^/]+/(.+?)(?:\.git)?/?$", u)
    if m:
        return m.group(1)
    return u


def _git_remote(path: str) -> str | None:
    try:
        out = subprocess.run(["git", "-C", path, "remote", "get-url", "origin"],
                             capture_output=True, text=True, timeout=5)
        return _canonical_remote(out.stdout.strip() or None)
    except Exception:
        return None

def _detect_org(name: str) -> str:
    """Derive org from project directory name. Most specific signals checked first."""
    n = name.lower()
    ado_signals = [
        "ado", "akademie", "datev", "cs-excel", "controlling", "seminar",
        "workshop", "campaigns", "entsorgung", "vku", "kweu", "mailings",
    ]
    om_signals = [
        "omnipulse", "obladenmedia", "pitchpage", "pitch-", "echoflow",
        "echomeet", "wunschguru", "dfs-gateway", "om-seo", "social-wall",
        "pixplain", "ki-service", "wbd-sap", "s-com", "book-haug",
        "om-ausgaben", "om-produktivitaet", "om-skill", "boilerplate",
        "coolify-clients",
    ]
    do_signals = [
        "odin", "dojo", "mac", "ppt", "n8n", "nexus", "youtube", "millionaire",
        "do-brand", "daily-briefing", "notebooklm", "cc-demo", "higgsfield",
        "video-editor", "learn",
    ]
    if any(s in n for s in ado_signals):
        return "ado"
    if any(s in n for s in om_signals):
        return "om"
    if any(s in n for s in do_signals):
        return "do"
    return "unknown"


def _is_project(path: str) -> bool:
    markers = ("package.json", "composer.json", "pyproject.toml", "CLAUDE.md", "README.md")
    if any(os.path.isfile(os.path.join(path, m)) for m in markers):
        return True
    return os.path.exists(os.path.join(path, ".git"))

def scan_projects(root: str) -> list[dict]:
    rows: list[dict] = []
    for base, dirs, _ in os.walk(root):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
        if base == root:
            continue
        if _is_project(base):
            remote = _git_remote(base)
            rows.append({
                "name": os.path.basename(base),
                "path": remote or base,
                "fs_path": base,
                "org": _detect_org(os.path.basename(base)),
                "stack": _stack(base),
                "git_remote": remote,
                "purpose_oneliner": _purpose(base),
                "status": None,
                "last_scanned_at": datetime.now(timezone.utc).isoformat(),
            })
            dirs[:] = []  # nicht in Projekte rekursieren
    return rows

def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--root", required=True)
    p.add_argument("--registry-only", action="store_true")
    args = p.parse_args()
    rows = scan_projects(args.root)
    for r in rows:
        upsert_project(r)
    print(f"upserted {len(rows)} projects")
    if not args.registry_only:
        res = ingest_embeddings(rows)
        print(f"embedded {res.embedded} chunks "
              f"(skipped={res.skipped_unchanged} errors={res.errors})")

if __name__ == "__main__":
    main()
