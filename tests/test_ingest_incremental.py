"""Tests fuer den inkrementellen Ingest (SP-3.1): nur geaenderte/neue/geloeschte
Dateien beruehren Azure. Qdrant + Embeddings gemockt."""
from unittest.mock import MagicMock, patch

import scripts.ingest as ing


def _run(tmp_path, existing, files):
    """Ingestiert tmp_path mit vorgegebenem existing-Hashmap und Datei-Set.

    Returns (total_chunks, calls, emb) wobei calls['deleted'] / calls['upserted']
    die logischen Pfade der delete_by_source_path- bzw. upsert-Aufrufe sind.
    """
    for name, content in files.items():
        (tmp_path / name).write_text(content)
    row = {"name": "x", "path": "OuPDO/x", "fs_path": str(tmp_path),
           "git_remote": "OuPDO/x", "org": "do"}
    emb = MagicMock()
    emb.embed_documents.side_effect = lambda chunks: [[0.0] * 1536 for _ in chunks]
    client = MagicMock()
    calls = {"deleted": [], "upserted": []}
    client.upsert.side_effect = lambda **k: calls["upserted"].append(
        k["points"][0].payload["source_path"])
    with patch.object(ing, "get_embeddings", return_value=emb), \
         patch.object(ing, "get_client", return_value=client), \
         patch.object(ing, "ensure_collection"), \
         patch.object(ing, "existing_hashes", return_value=existing), \
         patch.object(ing, "delete_by_source_path",
                      side_effect=lambda c, o, sp: calls["deleted"].append(sp)):
        total = ing.ingest_embeddings([row])
    return total, calls, emb


def test_file_hash_deterministic_and_content_sensitive():
    assert ing._file_hash("abc") == ing._file_hash("abc")
    assert ing._file_hash("abc") != ing._file_hash("abd")


def test_new_file_embeds_no_delete(tmp_path):
    total, calls, emb = _run(tmp_path, existing={}, files={"README.md": "hallo welt"})
    assert emb.embed_documents.called
    assert calls["deleted"] == []
    assert "OuPDO/x/README.md" in calls["upserted"]
    assert total >= 1


def test_unchanged_file_skips_no_embed_no_delete(tmp_path):
    text = "hallo welt"
    h = ing._file_hash(text)
    total, calls, emb = _run(tmp_path, existing={"OuPDO/x/README.md": h},
                             files={"README.md": text})
    emb.embed_documents.assert_not_called()
    assert calls["deleted"] == []
    assert calls["upserted"] == []
    assert total == 0


def test_changed_file_deletes_then_embeds(tmp_path):
    total, calls, emb = _run(tmp_path, existing={"OuPDO/x/README.md": "OLDHASH"},
                             files={"README.md": "voellig neuer inhalt"})
    assert "OuPDO/x/README.md" in calls["deleted"]
    assert emb.embed_documents.called
    assert "OuPDO/x/README.md" in calls["upserted"]


def test_orphan_file_deleted_when_gone_on_disk(tmp_path):
    text = "a content"
    h = ing._file_hash(text)
    total, calls, emb = _run(
        tmp_path,
        existing={"OuPDO/x/A.md": h, "OuPDO/x/B.md": "whatever"},
        files={"A.md": text},  # B.md nicht mehr on-disk
    )
    emb.embed_documents.assert_not_called()  # A unveraendert
    assert calls["deleted"] == ["OuPDO/x/B.md"]
    assert calls["upserted"] == []
    assert total == 0


def test_legacy_point_without_hash_reembeds(tmp_path):
    # Legacy: existing hat "" als Hash -> mismatch -> delete + re-embed
    total, calls, emb = _run(tmp_path, existing={"OuPDO/x/README.md": ""},
                             files={"README.md": "hallo"})
    assert "OuPDO/x/README.md" in calls["deleted"]
    assert emb.embed_documents.called
    assert "OuPDO/x/README.md" in calls["upserted"]
