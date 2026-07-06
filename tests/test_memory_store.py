from unittest.mock import MagicMock, patch

import pytest

import memory.store as store


def _patched():
    """Patcht Postgres + Qdrant + Embeddings; gibt (emb, client, points, drops) zurueck."""
    emb = MagicMock()
    emb.embed_query.return_value = [0.0] * 1536
    client = MagicMock()
    points: list[str] = []
    drops: list[str] = []
    return emb, client, points, drops


def test_remember_inserts_embeds_and_upserts_point(fake_supabase):
    db = fake_supabase(select_rows=[])  # kein bestehender Eintrag
    emb, client, points, drops = _patched()
    with patch.object(store, "get_supabase", return_value=db), \
         patch.object(store, "get_embeddings", return_value=emb), \
         patch.object(store, "get_client", return_value=client), \
         patch.object(store, "ensure_memory_collection"), \
         patch.object(store, "upsert_memory_point",
                      side_effect=lambda c, mid, v, p: points.append(mid)):
        res = store.remember("David fokussiert auf ODIN", subject="David", key="current_focus", org="do")
    assert res["action"] == "insert"
    assert db.inserts[0]["content"] == "David fokussiert auf ODIN"
    assert db.inserts[0]["key"] == "current_focus"
    assert db.inserts[0]["content_hash"] == store._hash("David fokussiert auf ODIN")
    assert emb.embed_query.called
    assert points == [res["id"]]


def test_remember_idempotent_noop_when_same_hash(fake_supabase):
    content = "David fokussiert auf ODIN"
    existing = [{"id": "mem-42", "content": content, "content_hash": store._hash(content),
                 "kind": "semantic", "subject": "David", "key": "current_focus", "org": "do"}]
    db = fake_supabase(select_rows=existing)
    emb, client, points, drops = _patched()
    with patch.object(store, "get_supabase", return_value=db), \
         patch.object(store, "get_embeddings", return_value=emb), \
         patch.object(store, "get_client", return_value=client), \
         patch.object(store, "ensure_memory_collection"), \
         patch.object(store, "upsert_memory_point",
                      side_effect=lambda c, mid, v, p: points.append(mid)):
        res = store.remember(content, subject="David", key="current_focus", org="do")
    assert res == {"id": "mem-42", "action": "noop"}
    assert db.inserts == []          # kein neuer Insert
    emb.embed_query.assert_not_called()  # kein Zweit-Embed
    assert points == []


def test_remember_supersede_when_key_content_changes(fake_supabase):
    existing = [{"id": "mem-7", "content": "alt", "content_hash": store._hash("alt"),
                 "kind": "semantic", "subject": "David", "key": "current_focus", "org": "do"}]
    db = fake_supabase(select_rows=existing)
    emb, client, points, drops = _patched()
    with patch.object(store, "get_supabase", return_value=db), \
         patch.object(store, "get_embeddings", return_value=emb), \
         patch.object(store, "get_client", return_value=client), \
         patch.object(store, "ensure_memory_collection"), \
         patch.object(store, "upsert_memory_point",
                      side_effect=lambda c, mid, v, p: points.append(mid)), \
         patch.object(store, "delete_point",
                      side_effect=lambda c, coll, pid: drops.append(pid)):
        res = store.remember("neu", subject="David", key="current_focus", org="do")
    assert res["action"] == "supersede"
    # Supersede laeuft als atomare RPC; die alte Identitaet wird als p_old_id uebergeben.
    assert len(db.rpc_calls) == 1
    name, params = db.rpc_calls[0]
    assert name == "odin_memory_supersede"
    assert params["p_old_id"] == "mem-7"
    assert params["p_content"] == "neu"
    assert drops == ["mem-7"]        # alter Point geloescht
    assert points == [res["id"]]     # neuer Point gesetzt


def test_remember_append_dedup_when_key_none(fake_supabase):
    # gleicher Inhalt existiert bereits (append/collection) -> No-op
    content = "Meeting mit Kunde X lief gut"
    existing = [{"id": "mem-5", "content": content, "content_hash": store._hash(content),
                 "kind": "episodic", "subject": "David", "key": None, "org": None}]
    db = fake_supabase(select_rows=existing)
    emb, client, points, drops = _patched()
    with patch.object(store, "get_supabase", return_value=db), \
         patch.object(store, "get_embeddings", return_value=emb), \
         patch.object(store, "get_client", return_value=client), \
         patch.object(store, "ensure_memory_collection"), \
         patch.object(store, "upsert_memory_point",
                      side_effect=lambda c, mid, v, p: points.append(mid)):
        res = store.remember(content, kind="episodic", subject="David")
    assert res == {"id": "mem-5", "action": "noop"}
    assert db.inserts == []


def test_remember_append_inserts_when_key_none(fake_supabase):
    # key=None + kein bestehender Eintrag gleichen Hashes -> Insert (Append-Pfad)
    db = fake_supabase(select_rows=[])
    emb, client, points, drops = _patched()
    with patch.object(store, "get_supabase", return_value=db), \
         patch.object(store, "get_embeddings", return_value=emb), \
         patch.object(store, "get_client", return_value=client), \
         patch.object(store, "ensure_memory_collection"), \
         patch.object(store, "upsert_memory_point",
                      side_effect=lambda c, mid, v, p: points.append(mid)):
        res = store.remember("Neues Episodenmemory ohne Key", kind="episodic", subject="David")
    assert res["action"] == "insert"
    assert db.inserts[0]["content"] == "Neues Episodenmemory ohne Key"
    assert db.inserts[0]["key"] is None
    assert emb.embed_query.called
    assert points == [res["id"]]


def test_remember_rejects_invalid_kind(fake_supabase):
    with pytest.raises(ValueError):
        store.remember("x", kind="bogus")
