from unittest.mock import MagicMock, patch

import pytest

import memory.store as store


def test_update_memory_by_id_supersedes(fake_supabase):
    old = {"id": "mem-99", "content": "alt", "content_hash": store._hash("alt"),
           "kind": "semantic", "subject": "David", "key": "role", "org": "do"}
    db = fake_supabase(select_rows=[old])
    drops, points, captured = [], [], []
    with patch.object(store, "get_supabase", return_value=db), \
         patch.object(store, "get_embeddings", return_value=MagicMock(embed_query=lambda q: [0.0])), \
         patch.object(store, "get_client", return_value=MagicMock()), \
         patch.object(store, "ensure_memory_collection"), \
         patch.object(store, "upsert_memory_point", side_effect=lambda c, mid, v, p: (points.append(mid), captured.append(p))), \
         patch.object(store, "delete_point", side_effect=lambda c, coll, pid: drops.append(pid)):
        res = store.update_memory("neu", id="mem-99")
    assert res["action"] == "supersede"
    assert len(db.rpc_calls) == 1
    name, params = db.rpc_calls[0]
    assert name == "odin_memory_supersede"
    assert params["p_old_id"] == "mem-99"
    assert drops == ["mem-99"]
    assert points == [res["id"]]
    assert captured[0]["valid_from"] == "2026-07-02T00:00:00+00:00"


def test_update_memory_noop_when_same_content(fake_supabase):
    same = "unveraendert"
    old = {"id": "mem-2", "content": same, "content_hash": store._hash(same),
           "kind": "semantic", "subject": "David", "key": "role", "org": "do"}
    db = fake_supabase(select_rows=[old])
    with patch.object(store, "get_supabase", return_value=db), \
         patch.object(store, "get_embeddings", return_value=MagicMock()) as emb:
        res = store.update_memory(same, id="mem-2")
    assert res == {"id": "mem-2", "action": "noop"}


def test_update_memory_requires_id_or_key():
    with pytest.raises(ValueError):
        store.update_memory("x")


def test_invalidate_sets_valid_to_and_drops_point(fake_supabase):
    db = fake_supabase()
    drops = []
    with patch.object(store, "get_supabase", return_value=db), \
         patch.object(store, "get_client", return_value=MagicMock()), \
         patch.object(store, "delete_point", side_effect=lambda c, coll, pid: drops.append(pid)):
        store.invalidate("mem-9")
    assert any(col == "id" and val == "mem-9" and "valid_to" in patch_
               for (col, val, patch_) in db.updates)
    assert drops == ["mem-9"]


def test_recall_about_returns_valid_rows(fake_supabase):
    rows = [{"id": "mem-1", "content": "a", "subject": "David"}]
    db = fake_supabase(select_rows=rows)
    with patch.object(store, "get_supabase", return_value=db):
        out = store.recall_about("David")
    assert out == rows


def test_search_memory_embeds_and_searches():
    emb = MagicMock(); emb.embed_query.return_value = [0.1]
    client = MagicMock()
    hit = MagicMock(); hit.payload = {"content": "gemerkt"}
    with patch.object(store, "get_embeddings", return_value=emb), \
         patch.object(store, "get_client", return_value=client), \
         patch.object(store, "search_memory_points", return_value=[hit]) as sp:
        out = store.search_memory("was weiss ich?", org="do")
    assert out == [hit]
    assert sp.call_args.kwargs["org"] == "do"


def test_search_memory_degrades_on_error():
    with patch.object(store, "get_embeddings", side_effect=RuntimeError("azure down")):
        out = store.search_memory("x")
    assert out == []


def test_recall_about_queries_valid_rows_by_subject():
    sentinel = [{"id": "mem-1", "content": "David ist Developer"}]
    client = MagicMock()
    (
        client.table.return_value
        .select.return_value
        .eq.return_value
        .is_.return_value
        .execute.return_value
        .data
    ) = sentinel
    with patch.object(store, "get_supabase", return_value=client):
        result = store.recall_about("David")
    assert result == sentinel
    client.table.assert_called_with("odin_memory")
    client.table.return_value.select.return_value.eq.assert_called_with("subject", "David")
    client.table.return_value.select.return_value.eq.return_value.is_.assert_called_with("valid_to", "null")


def test_update_memory_by_key_supersedes(fake_supabase):
    old = {"id": "mem-5", "content": "alt", "content_hash": store._hash("alt"),
           "kind": "semantic", "subject": "David", "key": "role", "org": "do"}
    db = fake_supabase(select_rows=[old])
    drops, points = [], []
    with patch.object(store, "get_supabase", return_value=db), \
         patch.object(store, "get_embeddings", return_value=MagicMock(embed_query=lambda q: [0.0])), \
         patch.object(store, "get_client", return_value=MagicMock()), \
         patch.object(store, "ensure_memory_collection"), \
         patch.object(store, "upsert_memory_point", side_effect=lambda c, mid, v, p: points.append(mid)), \
         patch.object(store, "delete_point", side_effect=lambda c, coll, pid: drops.append(pid)):
        res = store.update_memory("neu", subject="David", key="role", org="do")
    assert res["action"] == "supersede"
    assert len(db.rpc_calls) == 1
    name, params = db.rpc_calls[0]
    assert name == "odin_memory_supersede"
    assert params["p_old_id"] == "mem-5"
    assert drops == ["mem-5"]
    assert points == [res["id"]]


def test_update_memory_not_found_raises(fake_supabase):
    db = fake_supabase(select_rows=[])
    with patch.object(store, "get_supabase", return_value=db), \
         pytest.raises(ValueError):
        store.update_memory("x", id="nope")
