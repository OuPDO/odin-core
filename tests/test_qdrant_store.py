from unittest.mock import MagicMock

import knowledge.qdrant_store as qs


def test_normalize_url_adds_443_for_bare_https():
    from knowledge.qdrant_store import _normalize_url
    assert _normalize_url("https://qdrant.oblm.de") == "https://qdrant.oblm.de:443"
    assert _normalize_url("https://qdrant.oblm.de:443") == "https://qdrant.oblm.de:443"
    assert _normalize_url("http://qdrant:6333") == "http://qdrant:6333"


def test_ensure_collection_recreates_on_dim_mismatch():
    client = MagicMock()
    client.collection_exists.return_value = True
    client.get_collection.return_value.config.params.vectors.size = 999
    qs.ensure_collection(client, "om", 1536)
    client.delete_collection.assert_called_once_with("om_knowledge")
    client.create_collection.assert_called_once()


def test_delete_by_repo_filters_git_remote():
    client = MagicMock()
    qs.delete_by_repo(client, "do", "OuPDO/x")
    args, kwargs = client.delete.call_args
    assert kwargs["collection_name"] == "do_knowledge"
    # der Filter referenziert git_remote == OuPDO/x
    assert "OuPDO/x" in str(kwargs.get("points_selector") or kwargs)


def test_delete_by_source_path_filters_source_path():
    client = MagicMock()
    qs.delete_by_source_path(client, "do", "OuPDO/x/README.md")
    args, kwargs = client.delete.call_args
    assert kwargs["collection_name"] == "do_knowledge"
    assert "OuPDO/x/README.md" in str(kwargs.get("points_selector") or kwargs)


def test_existing_hashes_maps_source_path_to_hash_and_paginates():
    client = MagicMock()
    p1 = MagicMock(); p1.payload = {"source_path": "OuPDO/x/A.md", "content_hash": "aaa"}
    p2 = MagicMock(); p2.payload = {"source_path": "OuPDO/x/B.md", "content_hash": "bbb"}
    p3 = MagicMock(); p3.payload = {"source_path": "OuPDO/x/C.md"}  # legacy, kein hash
    # zwei Seiten: erste liefert next_offset, zweite None -> Ende
    client.scroll.side_effect = [([p1, p2], "cursor1"), ([p3], None)]
    out = qs.existing_hashes(client, "do", "OuPDO/x")
    assert out == {"OuPDO/x/A.md": "aaa", "OuPDO/x/B.md": "bbb", "OuPDO/x/C.md": ""}
    assert client.scroll.call_count == 2
