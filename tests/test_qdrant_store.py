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


def test_ensure_memory_collection_creates_when_absent():
    from qdrant_client.models import Distance
    client = MagicMock()
    client.collection_exists.return_value = False
    qs.ensure_memory_collection(client, 1536)
    client.create_collection.assert_called_once()
    name = client.create_collection.call_args.args[0] if client.create_collection.call_args.args \
        else client.create_collection.call_args.kwargs["collection_name"]
    assert name == "memory_knowledge"
    vectors_config = client.create_collection.call_args.kwargs["vectors_config"]
    assert vectors_config.distance == Distance.COSINE


def test_ensure_memory_collection_noop_when_dim_matches():
    client = MagicMock()
    client.collection_exists.return_value = True
    client.get_collection.return_value.config.params.vectors.size = 1536
    qs.ensure_memory_collection(client, 1536)
    client.delete_collection.assert_not_called()
    client.create_collection.assert_not_called()


def test_ensure_memory_collection_recreates_on_dim_mismatch():
    client = MagicMock()
    client.collection_exists.return_value = True
    client.get_collection.return_value.config.params.vectors.size = 999
    qs.ensure_memory_collection(client, 1536)
    client.delete_collection.assert_called_once_with("memory_knowledge")
    client.create_collection.assert_called_once()


def test_upsert_memory_point_uses_memory_id_as_point_id():
    client = MagicMock()
    qs.upsert_memory_point(client, "mem-1", [0.1, 0.2], {"kind": "semantic"})
    kwargs = client.upsert.call_args.kwargs
    assert kwargs["collection_name"] == "memory_knowledge"
    point = kwargs["points"][0]
    assert point.id == "mem-1"
    assert point.payload == {"kind": "semantic"}


def test_delete_point_selects_by_id():
    client = MagicMock()
    qs.delete_point(client, "memory_knowledge", "mem-9")
    kwargs = client.delete.call_args.kwargs
    assert kwargs["collection_name"] == "memory_knowledge"
    assert "mem-9" in str(kwargs["points_selector"])


def test_search_memory_points_filters_by_org_when_given():
    client = MagicMock()
    client.query_points.return_value.points = ["hit"]
    out = qs.search_memory_points(client, [0.0], org="om", top_k=3)
    kwargs = client.query_points.call_args.kwargs
    assert kwargs["collection_name"] == "memory_knowledge"
    assert kwargs["limit"] == 3
    assert "om" in str(kwargs["query_filter"])
    assert out == ["hit"]


def test_search_memory_points_no_filter_when_org_none():
    client = MagicMock()
    client.query_points.return_value.points = []
    qs.search_memory_points(client, [0.0], org=None)
    assert client.query_points.call_args.kwargs["query_filter"] is None


def test_existing_memory_hashes_maps_id_to_hash_and_paginates():
    client = MagicMock()
    p1 = MagicMock(); p1.id = "mem-1"; p1.payload = {"content_hash": "aaa"}
    p2 = MagicMock(); p2.id = "mem-2"; p2.payload = {"content_hash": "bbb"}
    p3 = MagicMock(); p3.id = "mem-3"; p3.payload = {}  # legacy ohne hash
    client.scroll.side_effect = [([p1, p2], "cur"), ([p3], None)]
    out = qs.existing_memory_hashes(client)
    assert out == {"mem-1": "aaa", "mem-2": "bbb", "mem-3": ""}
    assert client.scroll.call_count == 2
