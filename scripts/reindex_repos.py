"""Server-Entrypoint: Registered repos clonen/pullen, scannen, embedden.

Aufruf:
    python -m scripts.reindex_repos
    python scripts/reindex_repos.py
    CACHE_DIR=/data/cache python -m scripts.reindex_repos
"""
import base64
import logging
import os
import subprocess
import sys
import urllib.parse
import urllib.request
from datetime import datetime, timezone

# Macht den odin-core-Root importierbar, wenn das Skript direkt ausgefuehrt wird.
# Direktaufruf legt scripts/ auf sys.path, nicht das Package-Root.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config.indexed_repos import get_indexed_repos
from memory.registry import upsert_project
from scripts.ingest import _purpose, _stack, ingest_embeddings

logger = logging.getLogger("odin.reindex")


def _auth_args(token: str | None) -> list[str]:
    """Build git -c args that inject auth without touching .git/config."""
    if not token:
        return []
    b = base64.b64encode(f"x-access-token:{token}".encode()).decode()
    return ["-c", f"http.extraHeader=Authorization: Basic {b}"]


def _clone_or_pull(repo: str, branch: str, dest: str, token: str | None) -> str:
    """Clont ein Repo oder pullt den aktuellen Stand. Gibt dest zurueck.

    Token gelangt nie in .git/config -- per-command header, tokenlose URL.
    """
    url = f"https://github.com/{repo}.git"   # tokenless -> never persisted
    try:
        if os.path.isdir(os.path.join(dest, ".git")):
            subprocess.run(
                ["git", *_auth_args(token), "-C", dest, "pull", "--ff-only"],
                check=True,
                capture_output=True,
                text=True,
                timeout=120,
            )
        else:
            subprocess.run(
                ["git", *_auth_args(token), "clone", "--depth", "1", "-b", branch, url, dest],
                check=True,
                capture_output=True,
                text=True,
                timeout=180,
            )
    except subprocess.CalledProcessError as cpe:
        msg = (cpe.stderr or "")[:200]
        if token:
            msg = msg.replace(token, "***")
        raise RuntimeError(f"git failed (exit {cpe.returncode}): {msg}") from None
    return dest


def reindex(cache_dir: str) -> dict:
    """Iteriert ueber alle registrierten Repos, clont/pullt sie und ingestiert.

    Baut eine Zeile direkt aus der Config (kein re-scan des Cache).
    Identitaet = entry["repo"] (owner/repo, autoritativ).

    Returns:
        dict mit repos_ok, repos_failed, chunks
    """
    token = os.environ.get("GITHUB_TOKEN")
    ok = failed = chunks = skipped = errors = 0

    for entry in get_indexed_repos():
        repo = entry["repo"]
        dest = os.path.join(cache_dir, repo.replace("/", "__"))
        try:
            _clone_or_pull(repo, entry.get("branch", "main"), dest, token)
            row = {
                "name": repo.rsplit("/", 1)[-1],
                "path": repo,          # logische Identitaet = owner/repo (Config, autoritativ)
                "fs_path": dest,
                "git_remote": repo,
                "org": entry["org"],
                "stack": _stack(dest),
                "purpose_oneliner": _purpose(dest),
                "status": None,
                "last_scanned_at": datetime.now(timezone.utc).isoformat(),
            }
            upsert_project(row)
            res = ingest_embeddings([row])
            chunks += res.embedded
            skipped += res.skipped_unchanged
            errors += res.errors
            ok += 1
        except Exception as exc:
            logger.warning("repo %s failed: %s", repo, exc)
            failed += 1
            continue

    result = {"repos_ok": ok, "repos_failed": failed, "chunks": chunks,
              "skipped": skipped, "errors": errors}
    logger.info("reindex done: %s", result)
    return result


def _push_heartbeat(result: dict) -> None:
    """Meldet den Lauf an einen Uptime-Kuma-Push-Monitor (D-026-Muster).

    KUMA_REINDEX_PUSH_URL leer -> No-op. Degradiert (status=down, Kuma alarmiert)
    wenn Embeddings trotz Retries scheiterten (errors>0, typisch anhaltendes 429)
    oder ein Repo fehlschlug. Faengt das stille Versagen, bei dem der Job gruen
    endet aber 0 Chunks embeddet.
    """
    url = os.environ.get("KUMA_REINDEX_PUSH_URL")
    if not url:
        return
    degraded = result["errors"] > 0 or result["repos_failed"] > 0
    status = "down" if degraded else "up"
    msg = (f"embedded={result['chunks']} skipped={result['skipped']} "
           f"errors={result['errors']} repos_failed={result['repos_failed']}")
    query = urllib.parse.urlencode({"status": status, "msg": msg, "ping": ""})
    try:
        with urllib.request.urlopen(f"{url}?{query}", timeout=15) as resp:
            resp.read()
        logger.info("heartbeat pushed: status=%s %s", status, msg)
    except Exception as exc:
        logger.warning("heartbeat push failed: %s", exc)


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    cache = os.environ.get("CACHE_DIR", "/tmp/odin-reindex-cache")
    os.makedirs(cache, exist_ok=True)
    result = reindex(cache)
    logger.info("reindex summary: %s", result)
    _push_heartbeat(result)
    if result["repos_failed"]:
        sys.exit(1)


if __name__ == "__main__":
    main()
