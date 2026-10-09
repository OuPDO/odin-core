"""Tests fuer den Wisdom-JSONL-Ingest. Qdrant und Embeddings gemockt, kein Live-Zugriff."""
import json
from unittest.mock import MagicMock, patch

import pytest

import scripts.ingest_wisdom as iw

REC = {
    "id": "abc-q1", "text": "Raise prices before you add features.\n\nHebt Preise zuerst.\n\nquote",
    "lesson_en": "Raise prices", "lesson_de": "Preise heben", "answer_en": "quote", "answer_de": "Zitat",
    "theme": ["pricing"], "person": "Alex Hormozi", "channel": "alex-hormozi",
    "source_url": "https://www.youtube.com/watch?v=abc&t=12s", "confidence": "high",
    "published": "2024-05-01", "start_seconds": 12, "stance": None,
}


def _rec(**kw):
    return {**REC, **kw}


def test_point_id_is_deterministic_and_unique_per_insight():
    assert iw.point_id("abc-q1") == iw.point_id("abc-q1")
    assert iw.point_id("abc-q1") != iw.point_id("abc-q2")


def test_validate_requires_id_text_and_timestamped_source():
    iw.validate_record(REC)
    with pytest.raises(ValueError):
        iw.validate_record(_rec(id=""))
    with pytest.raises(ValueError):
        iw.validate_record(_rec(source_url="https://www.youtube.com/watch?v=abc"))


def test_payload_carries_filter_fields_and_contract():
    p = iw.build_payload(REC)
    assert p["typ"] == "wisdom" and p["project"] == "learn-from-millionaires"
    assert p["source_path"] == "abc-q1" and p["chunk_text"] == REC["text"]
    assert p["theme"] == ["pricing"] and p["published"] == "2024-05-01"


def test_hash_changes_when_date_changes_but_not_otherwise():
    assert iw.content_hash(REC) == iw.content_hash(dict(REC))
    assert iw.content_hash(REC) != iw.content_hash(_rec(published="2023-01-01"))


def test_duplicate_ids_rejected():
    with pytest.raises(ValueError):
        iw.build_points([REC, dict(REC)])


def test_default_is_dry_run_and_touches_nothing():
    with patch.object(iw, "get_client") as gc, patch.object(iw, "get_embeddings") as ge:
        out = iw.ingest([REC], apply=False)
    assert out == {"records": 1, "embedded": 0, "skipped": 0, "deleted": 0,
                   "applied": False, "collection": "wisdom_knowledge"}
    gc.assert_not_called()
    ge.assert_not_called()


def test_apply_skips_unchanged_embeds_changed_and_prunes_orphans():
    unchanged = _rec(id="same-q1", source_url="https://www.youtube.com/watch?v=same&t=1s")
    changed = _rec(id="new-q1", source_url="https://www.youtube.com/watch?v=new&t=1s")
    existing = {"same-q1": iw.content_hash(unchanged), "gone-q1": "x"}
    client = MagicMock()
    emb = MagicMock()
    with patch.object(iw, "get_client", return_value=client), \
         patch.object(iw, "get_embeddings", return_value=emb), \
         patch.object(iw, "ensure_wisdom_collection"), \
         patch.object(iw, "existing_wisdom_hashes", return_value=existing), \
         patch.object(iw, "_embed_batched", side_effect=lambda e, texts: [[0.0] for _ in texts]), \
         patch.object(iw, "_upsert_with_retry") as up, \
         patch.object(iw, "delete_wisdom_by_source_path") as dele:
        out = iw.ingest([unchanged, changed], apply=True)
    assert out["skipped"] == 1 and out["embedded"] == 1 and out["deleted"] == 1
    dele.assert_called_once_with(client, "gone-q1")
    points = up.call_args[0][2]
    assert [p.payload["source_path"] for p in points] == ["new-q1"]


def test_load_jsonl_roundtrip(tmp_path):
    p = tmp_path / "w.jsonl"
    p.write_text(json.dumps(REC, ensure_ascii=False) + "\n\n", encoding="utf-8")
    assert iw.load_jsonl(str(p)) == [REC]
