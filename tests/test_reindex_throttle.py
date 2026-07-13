"""Tests fuer den 429-Fix: geduldiger Retry surfaced als errors (kein Crash) und
der Kuma-Heartbeat, der das stille Versagen faengt. Qdrant + Embeddings gemockt."""
from unittest.mock import MagicMock, patch

import scripts.ingest as ing
import scripts.reindex_repos as rr


def _mk(tmp_path, existing, files, embed_raises=False):
    """Ingestiert tmp_path und gibt (result, calls) zurueck."""
    for name, content in files.items():
        (tmp_path / name).write_text(content)
    row = {"name": "x", "path": "OuPDO/x", "fs_path": str(tmp_path),
           "git_remote": "OuPDO/x", "org": "do"}
    emb = MagicMock()
    if embed_raises:
        emb.embed_documents.side_effect = RuntimeError("429 RateLimitReached")
    else:
        emb.embed_documents.side_effect = lambda chunks: [[0.0] * 1536 for _ in chunks]
    client = MagicMock()
    calls = {"deleted": [], "upserted": []}
    client.upsert.side_effect = lambda **k: calls["upserted"].append(
        k["points"][0].payload["source_path"])
    with patch.object(ing, "get_embeddings", return_value=emb), \
         patch.object(ing, "get_client", return_value=client), \
         patch.object(ing, "ensure_collection"), \
         patch.object(ing, "existing_hashes", return_value=existing), \
         patch.object(ing, "time") as mock_time, \
         patch.object(ing.settings, "embed_min_interval_seconds", 0.0), \
         patch.object(ing, "delete_by_source_path",
                      side_effect=lambda c, o, sp: calls["deleted"].append(sp)):
        mock_time.sleep = MagicMock()  # keine echten Backoff-Waits im Test
        result = ing.ingest_embeddings([row])
    return result, calls, emb


def test_embed_failure_counts_as_error_not_crash(tmp_path):
    """Anhaltendes 429 -> Quelle wird uebersprungen und als error gezaehlt,
    kein Crash, embedded=0, Datei behaelt keinen Hash (kein Upsert)."""
    res, calls, emb = _mk(tmp_path, existing={}, files={"A.md": "content a"},
                          embed_raises=True)
    assert res.embedded == 0
    assert res.errors == 1
    assert calls["upserted"] == []


def test_embed_failure_retries_before_giving_up(tmp_path):
    """_embed_with_retry versucht settings.embed_max_attempts mal, bevor es die
    Quelle als error zaehlt."""
    from config.settings import settings
    res, calls, emb = _mk(tmp_path, existing={}, files={"A.md": "content a"},
                          embed_raises=True)
    assert emb.embed_documents.call_count == settings.embed_max_attempts


def test_orphan_still_deleted(tmp_path):
    """Ohne Budget-Logik wird ein Orphan (indexiert, nicht mehr on-disk) geloescht."""
    res, calls, emb = _mk(
        tmp_path,
        existing={"OuPDO/x/GHOST.md": "h"},
        files={"A.md": "content a"},
    )
    assert "OuPDO/x/GHOST.md" in calls["deleted"]


def test_no_sleep_after_final_attempt(tmp_path):
    """Retry-Loop schlaeft nur zwischen Versuchen, nicht nach dem letzten
    (kein unnoetiger Backoff vor dem Aufgeben)."""
    from config.settings import settings
    (tmp_path / "A.md").write_text("content a")
    row = {"name": "x", "path": "OuPDO/x", "fs_path": str(tmp_path),
           "git_remote": "OuPDO/x", "org": "do"}
    emb = MagicMock()
    emb.embed_documents.side_effect = RuntimeError("429 RateLimitReached")
    with patch.object(ing, "get_embeddings", return_value=emb), \
         patch.object(ing, "get_client", return_value=MagicMock()), \
         patch.object(ing, "ensure_collection"), \
         patch.object(ing, "existing_hashes", return_value={}), \
         patch.object(ing, "delete_by_source_path"), \
         patch.object(ing.settings, "embed_min_interval_seconds", 0.0), \
         patch.object(ing, "time") as mt:
        ing.ingest_embeddings([row])
    assert emb.embed_documents.call_count == settings.embed_max_attempts
    assert mt.sleep.call_count == settings.embed_max_attempts - 1


def test_orphan_delete_failure_counts_as_error(tmp_path):
    """Scheitert das Loeschen eines Orphans, wird es als error gezaehlt -> Heartbeat rot."""
    (tmp_path / "A.md").write_text("content a")
    row = {"name": "x", "path": "OuPDO/x", "fs_path": str(tmp_path),
           "git_remote": "OuPDO/x", "org": "do"}
    emb = MagicMock()
    emb.embed_documents.side_effect = lambda chunks: [[0.0] * 1536 for _ in chunks]
    with patch.object(ing, "get_embeddings", return_value=emb), \
         patch.object(ing, "get_client", return_value=MagicMock()), \
         patch.object(ing, "ensure_collection"), \
         patch.object(ing, "existing_hashes", return_value={"OuPDO/x/GHOST.md": "h"}), \
         patch.object(ing, "time"), \
         patch.object(ing.settings, "embed_min_interval_seconds", 0.0), \
         patch.object(ing, "delete_by_source_path", side_effect=RuntimeError("qdrant down")):
        res = ing.ingest_embeddings([row])
    assert res.errors >= 1


def test_embed_splits_into_subbatches(tmp_path, monkeypatch):
    """Grosse Datei -> Chunks werden in Sub-Batches <= embed_batch_size embedded,
    nicht als EIN Riesen-Request (der das S0-Per-Request-Limit reisst)."""
    monkeypatch.setattr(ing.settings, "embed_batch_size", 4)
    big = "x" * (800 * 12)  # -> >= 12 Chunks (size 800, overlap 100)
    res, calls, emb = _mk(tmp_path, existing={}, files={"A.md": big})
    assert emb.embed_documents.call_count >= 3          # 12 Chunks / 4 -> >= 3 Requests
    for c in emb.embed_documents.call_args_list:
        assert len(c.args[0]) <= 4                       # kein Batch groesser als die Grenze
    assert res.embedded >= 12                            # alle Chunks embedded + upserted


def test_pace_enforces_min_interval(monkeypatch):
    """_pace schlaeft, bis embed_min_interval_seconds seit dem letzten Request um
    sind -- proaktive Drossel unabhaengig vom 429-Backoff."""
    monkeypatch.setattr(ing.settings, "embed_min_interval_seconds", 3.0)
    clock = {"t": 100.0}
    slept: list[float] = []
    monkeypatch.setattr(ing.time, "monotonic", lambda: clock["t"])
    monkeypatch.setattr(ing.time, "sleep", lambda s: slept.append(s))
    ing._last_embed_at[0] = 0.0
    ing._pace()                     # erster Call: last=0 -> lange her -> kein Sleep
    assert slept == []
    ing._pace()                     # sofortiger zweiter Call -> volle 3s warten
    assert slept and abs(slept[-1] - 3.0) < 0.01


def test_pace_disabled_when_interval_zero(monkeypatch):
    """interval <= 0 -> Drossel aus, kein Sleep (Backoff bleibt separat)."""
    monkeypatch.setattr(ing.settings, "embed_min_interval_seconds", 0.0)
    slept: list[float] = []
    monkeypatch.setattr(ing.time, "sleep", lambda s: slept.append(s))
    ing._last_embed_at[0] = 0.0
    ing._pace()
    ing._pace()
    assert slept == []


def _row(tmp_path):
    return {"name": "x", "path": "OuPDO/x", "fs_path": str(tmp_path),
            "git_remote": "OuPDO/x", "org": "do"}


def test_upsert_splits_into_batches(tmp_path, monkeypatch):
    """Grosse Datei -> Points werden in Upsert-Batches <= upsert_batch_size
    geschrieben, nicht als EIN Riesen-PUT (der das Proxy-Body-Limit reisst -> 502)."""
    monkeypatch.setattr(ing.settings, "upsert_batch_size", 5)
    monkeypatch.setattr(ing.settings, "embed_min_interval_seconds", 0.0)
    (tmp_path / "A.md").write_text("x" * (800 * 12))  # >= 12 Chunks
    sizes: list[int] = []
    emb = MagicMock()
    emb.embed_documents.side_effect = lambda c: [[0.0] * 1536 for _ in c]
    client = MagicMock()
    client.upsert.side_effect = lambda **k: sizes.append(len(k["points"]))
    with patch.object(ing, "get_embeddings", return_value=emb), \
         patch.object(ing, "get_client", return_value=client), \
         patch.object(ing, "ensure_collection"), \
         patch.object(ing, "existing_hashes", return_value={}), \
         patch.object(ing, "time"), \
         patch.object(ing, "delete_by_source_path"):
        res = ing.ingest_embeddings([_row(tmp_path)])
    assert len(sizes) >= 3                 # 12 Points / 5 -> >= 3 Batches
    assert all(s <= 5 for s in sizes)      # kein Batch groesser als die Grenze
    assert res.errors == 0


def test_upsert_failure_rolls_back_and_counts_error(tmp_path, monkeypatch):
    """Scheitert ein Upsert-Batch endgueltig -> Rollback (delete_by_source_path der
    Quelle) + error, kein inkonsistenter Teilstand mit content_hash."""
    monkeypatch.setattr(ing.settings, "upsert_batch_size", 5)
    monkeypatch.setattr(ing.settings, "upsert_max_attempts", 2)
    monkeypatch.setattr(ing.settings, "embed_min_interval_seconds", 0.0)
    (tmp_path / "A.md").write_text("x" * (800 * 12))
    emb = MagicMock()
    emb.embed_documents.side_effect = lambda c: [[0.0] * 1536 for _ in c]
    client = MagicMock()
    n = {"i": 0}

    def _upsert(**k):
        n["i"] += 1
        if n["i"] >= 2:                    # ab dem zweiten Batch dauerhaft 502
            raise RuntimeError("Unexpected Response: 502 (Bad Gateway)")

    client.upsert.side_effect = _upsert
    deleted: list[str] = []
    with patch.object(ing, "get_embeddings", return_value=emb), \
         patch.object(ing, "get_client", return_value=client), \
         patch.object(ing, "ensure_collection"), \
         patch.object(ing, "existing_hashes", return_value={}), \
         patch.object(ing, "time"), \
         patch.object(ing, "delete_by_source_path",
                      side_effect=lambda c, o, sp: deleted.append(sp)):
        res = ing.ingest_embeddings([_row(tmp_path)])
    assert res.errors == 1
    assert res.embedded == 0
    assert "OuPDO/x/A.md" in deleted       # Rollback ausgefuehrt


def test_rollback_delete_retries(tmp_path, monkeypatch):
    """Scheitert der Rollback-Delete transient, wird er wiederholt -- sonst blieben
    Teil-Points MIT content_hash liegen und die Quelle waere permanent geskippt."""
    monkeypatch.setattr(ing.settings, "upsert_batch_size", 5)
    monkeypatch.setattr(ing.settings, "upsert_max_attempts", 3)
    monkeypatch.setattr(ing.settings, "embed_min_interval_seconds", 0.0)
    (tmp_path / "A.md").write_text("x" * (800 * 12))
    emb = MagicMock()
    emb.embed_documents.side_effect = lambda c: [[0.0] * 1536 for _ in c]
    client = MagicMock()
    up = {"i": 0}

    def _upsert(**k):
        up["i"] += 1
        if up["i"] >= 2:                    # zweiter Batch scheitert -> Rollback
            raise RuntimeError("502")

    client.upsert.side_effect = _upsert
    dele = {"i": 0}

    def _del(c, o, sp):
        dele["i"] += 1
        if dele["i"] == 1:                  # erster Rollback-Delete scheitert transient
            raise RuntimeError("qdrant blip")

    with patch.object(ing, "get_embeddings", return_value=emb), \
         patch.object(ing, "get_client", return_value=client), \
         patch.object(ing, "ensure_collection"), \
         patch.object(ing, "existing_hashes", return_value={}), \
         patch.object(ing, "time"), \
         patch.object(ing, "delete_by_source_path", side_effect=_del):
        res = ing.ingest_embeddings([_row(tmp_path)])
    assert res.errors == 1
    assert dele["i"] >= 2                   # Rollback-Delete wurde wiederholt


def test_upsert_retries_transient_then_succeeds(tmp_path, monkeypatch):
    """Transientes Qdrant-5xx auf einem Batch -> kurzer Retry, dann Erfolg,
    kein error."""
    monkeypatch.setattr(ing.settings, "upsert_max_attempts", 3)
    monkeypatch.setattr(ing.settings, "embed_min_interval_seconds", 0.0)
    (tmp_path / "A.md").write_text("content a")
    emb = MagicMock()
    emb.embed_documents.side_effect = lambda c: [[0.0] * 1536 for _ in c]
    client = MagicMock()
    n = {"i": 0}

    def _upsert(**k):
        n["i"] += 1
        if n["i"] == 1:
            raise RuntimeError("502 transient")

    client.upsert.side_effect = _upsert
    with patch.object(ing, "get_embeddings", return_value=emb), \
         patch.object(ing, "get_client", return_value=client), \
         patch.object(ing, "ensure_collection"), \
         patch.object(ing, "existing_hashes", return_value={}), \
         patch.object(ing, "time"), \
         patch.object(ing, "delete_by_source_path"):
        res = ing.ingest_embeddings([_row(tmp_path)])
    assert res.errors == 0
    assert res.embedded >= 1
    assert n["i"] == 2                      # 1 Fehlschlag + 1 Erfolg


def _capture_heartbeat(monkeypatch, result):
    seen = {}
    monkeypatch.setenv("KUMA_REINDEX_PUSH_URL", "https://kuma.example/api/push/abc")

    class _Resp:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def read(self): return b""

    def _fake_urlopen(url, timeout=0):
        seen["url"] = url
        return _Resp()

    monkeypatch.setattr(rr.urllib.request, "urlopen", _fake_urlopen)
    rr._push_heartbeat(result)
    return seen.get("url", "")


def test_heartbeat_down_when_errors(monkeypatch):
    url = _capture_heartbeat(monkeypatch, {
        "chunks": 0, "skipped": 5, "errors": 3, "repos_failed": 0})
    assert "status=down" in url


def test_heartbeat_up_when_clean(monkeypatch):
    url = _capture_heartbeat(monkeypatch, {
        "chunks": 42, "skipped": 5, "errors": 0, "repos_failed": 0})
    assert "status=up" in url


def test_heartbeat_noop_without_url(monkeypatch):
    monkeypatch.delenv("KUMA_REINDEX_PUSH_URL", raising=False)
    called = {"n": 0}
    monkeypatch.setattr(rr.urllib.request, "urlopen",
                        lambda *a, **k: called.__setitem__("n", called["n"] + 1))
    rr._push_heartbeat({"chunks": 0, "skipped": 0, "errors": 0, "repos_failed": 0})
    assert called["n"] == 0
