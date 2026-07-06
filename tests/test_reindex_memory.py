from unittest.mock import MagicMock, patch

import scripts.reindex_memory as rm


def _db_with(rows):
    db = MagicMock()
    db.table.return_value.select.return_value.is_.return_value.execute.return_value.data = rows
    return db


def test_reindex_memory_embeds_valid_rows():
    rows = [
        {"id": "mem-1", "content": "a", "content_hash": "h1", "kind": "semantic",
         "subject": "David", "key": None, "org": "do", "valid_from": "t1"},
        {"id": "mem-2", "content": "b", "content_hash": "h2", "kind": "episodic",
         "subject": "David", "key": None, "org": None, "valid_from": "t2"},
    ]
    emb = MagicMock(); emb.embed_query.side_effect = lambda c: [0.0] * 1536
    client = MagicMock()
    upserts = []
    with patch.object(rm, "get_supabase", return_value=_db_with(rows)), \
         patch.object(rm, "get_embeddings", return_value=emb), \
         patch.object(rm, "get_client", return_value=client), \
         patch.object(rm, "ensure_memory_collection"), \
         patch.object(rm, "existing_memory_hashes", return_value={}), \
         patch.object(rm, "upsert_memory_point",
                      side_effect=lambda c, mid, v, p: upserts.append(mid)):
        res = rm.reindex_memory()
    assert res == {"valid": 2, "indexed": 2, "skipped": 0, "failed": 0, "pruned": 0}
    assert upserts == ["mem-1", "mem-2"]


def test_reindex_memory_skips_unchanged_hash():
    rows = [{"id": "mem-1", "content": "a", "content_hash": "h1", "kind": "semantic",
             "subject": "David", "key": None, "org": "do", "valid_from": "t1"}]
    emb = MagicMock()
    with patch.object(rm, "get_supabase", return_value=_db_with(rows)), \
         patch.object(rm, "get_embeddings", return_value=emb), \
         patch.object(rm, "get_client", return_value=MagicMock()), \
         patch.object(rm, "ensure_memory_collection"), \
         patch.object(rm, "existing_memory_hashes", return_value={"mem-1": "h1"}), \
         patch.object(rm, "upsert_memory_point"):
        res = rm.reindex_memory()
    assert res == {"valid": 1, "indexed": 0, "skipped": 1, "failed": 0, "pruned": 0}
    emb.embed_query.assert_not_called()


def test_reindex_memory_counts_failed_rows():
    rows = [
        {"id": "mem-1", "content": "a", "content_hash": "h1", "kind": "semantic",
         "subject": "David", "key": None, "org": "do", "valid_from": "t1"},
        {"id": "mem-2", "content": "b", "content_hash": "h2", "kind": "episodic",
         "subject": "David", "key": None, "org": None, "valid_from": "t2"},
    ]
    emb = MagicMock()
    # First call raises, second succeeds.
    emb.embed_query.side_effect = [RuntimeError("embed failed"), [0.0] * 1536]
    upserts = []
    with patch.object(rm, "get_supabase", return_value=_db_with(rows)), \
         patch.object(rm, "get_embeddings", return_value=emb), \
         patch.object(rm, "get_client", return_value=MagicMock()), \
         patch.object(rm, "ensure_memory_collection"), \
         patch.object(rm, "existing_memory_hashes", return_value={}), \
         patch.object(rm, "upsert_memory_point",
                      side_effect=lambda c, mid, v, p: upserts.append(mid)):
        res = rm.reindex_memory()
    # Second row must still be upserted (loop did not abort).
    assert upserts == ["mem-2"]
    assert res["failed"] == 1
    assert res["indexed"] == 1
    assert res["skipped"] == 0
    assert res["indexed"] + res["skipped"] + res["failed"] == res["valid"]


def test_valid_memories_queries_valid_odin_memory_rows():
    sentinel_rows = [{"id": "mem-x", "content": "x"}]
    client = MagicMock()
    (
        client.table.return_value
        .select.return_value
        .is_.return_value
        .execute.return_value
        .data
    ) = sentinel_rows

    with patch.object(rm, "get_supabase", return_value=client):
        result = rm.valid_memories()

    assert result == sentinel_rows
    client.table.assert_called_with("odin_memory")
    is_call = client.table.return_value.select.return_value.is_
    is_call.assert_called_with("valid_to", "null")


def test_reindex_memory_prunes_stale_points():
    rows = [{"id": "mem-1", "content": "a", "content_hash": "h1", "kind": "semantic",
             "subject": "David", "key": None, "org": "do", "valid_from": "t1"}]
    emb = MagicMock()
    deleted = []
    with patch.object(rm, "get_supabase", return_value=_db_with(rows)), \
         patch.object(rm, "get_embeddings", return_value=emb), \
         patch.object(rm, "get_client", return_value=MagicMock()), \
         patch.object(rm, "ensure_memory_collection"), \
         patch.object(rm, "existing_memory_hashes", return_value={"mem-1": "h1", "mem-OLD": "hZ"}), \
         patch.object(rm, "upsert_memory_point"), \
         patch.object(rm, "delete_point", side_effect=lambda c, coll, pid: deleted.append(pid)):
        res = rm.reindex_memory()
    emb.embed_query.assert_not_called()
    assert res["pruned"] == 1
    assert deleted == ["mem-OLD"]
