from unittest.mock import MagicMock, patch

import scripts.ingest as ing


def test_ingest_new_file_upserts_with_logical_path_and_content_hash(tmp_path):
    """Neue Datei (nicht in existing): embed + upsert, kein per-file Delete."""
    (tmp_path / "README.md").write_text("hallo welt")
    row = {"name": "x", "path": "OuPDO/x", "fs_path": str(tmp_path),
           "git_remote": "OuPDO/x", "org": "do"}
    emb = MagicMock()
    emb.embed_documents.return_value = [[0.0] * 1536]
    client = MagicMock()
    with patch.object(ing, "get_embeddings", return_value=emb), \
         patch.object(ing, "get_client", return_value=client), \
         patch.object(ing, "ensure_collection"), \
         patch.object(ing, "existing_hashes", return_value={}), \
         patch.object(ing, "delete_by_source_path") as dsp:
        ing.ingest_embeddings([row])
    dsp.assert_not_called()  # neue Datei -> kein Delete
    up = client.upsert.call_args
    pt = up.kwargs["points"][0]
    assert pt.payload["source_path"].startswith("OuPDO/x/")
    assert pt.payload["git_remote"] == "OuPDO/x"
    assert pt.payload["content_hash"]  # gesetzt
