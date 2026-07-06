"""Tests fuer den Strategy-SSoT JSONL-Ingest (Plan B, 2026-07-02).

Routing: portfolio/positionierung nach payload.entitaet; ankunftspunkt (entitaet=null)
nach Ziel-Site aus dem Text. Qdrant + Embeddings gemockt. Ein optionaler Full-Dataset-
Check laeuft nur, wenn das echte Export-File lokal vorliegt.
"""
import os
from unittest.mock import MagicMock, patch

import pytest

import scripts.ingest_strategy as si

# --- repraesentative echte Records (aus strategy_knowledge.jsonl) ---
PORTFOLIO_OM = {
    "text": "KI für Führungskräfte (Managed AI Employee) (OM, premium): DSGVO-konformer ...",
    "payload": {"typ": "strategy", "kind": "portfolio", "entitaet": "OM", "ref": "ki-fuehrungskraefte"},
}
PORTFOLIO_ADO = {
    "text": "KI-Readiness-Audit + Enablement-Workshop (ADO, tueroeffner): Niedrigschwelliger ...",
    "payload": {"typ": "strategy", "kind": "portfolio", "entitaet": "ADO", "ref": "ki-audit"},
}
POS_CORE_OM = {
    "text": "Positionierung (core): Wir schließen die Lücke zwischen KI-Adoption und KI-Wertschöpfung ...",
    "payload": {"typ": "strategy", "kind": "positionierung", "entitaet": "OM", "scope": "core"},
}
POS_DO = {
    "text": "Positionierung (DO): David Obladen — der KI-Experte, der in Public baut ...",
    "payload": {"typ": "strategy", "kind": "positionierung", "entitaet": "DO", "scope": "DO"},
}
ANK_DAVID = {
    "text": "Ankunftspunkt davidobladen.de für gf-clevel: KI als strategischer Wettbewerbsvorteil.\n Angebot: ki-fuehrungskraefte. CTA: Erstgespräch vereinbaren. Nächster Schritt: KI-Audit.",
    "payload": {"typ": "strategy", "kind": "ankunftspunkt", "entitaet": None, "segment": "gf-clevel"},
}
ANK_ADO = {
    "text": "Ankunftspunkt ado-site für kommunen: KI-Kompetenz für Ihre Belegschaft.\n Angebot: enablement. CTA: Kursübersicht ansehen. Nächster Schritt: Readiness-Check.",
    "payload": {"typ": "strategy", "kind": "ankunftspunkt", "entitaet": None, "segment": "kommunen"},
}
ANK_OM_SITE = {
    "text": "Ankunftspunkt om-site für solo-klein: Deine KI-Website in Tagen statt Wochen.\n Angebot: pitchpage. CTA: Demo ansehen. Nächster Schritt: Setup buchen.",
    "payload": {"typ": "strategy", "kind": "ankunftspunkt", "entitaet": None, "segment": "solo-klein"},
}
ANK_LANDING = {
    "text": "Ankunftspunkt landing:ki-agenten für gf-clevel: Autonome KI-Prozesse übernehmen Routineaufgaben.\n Angebot: agenten-orchestrierung. CTA: Beratung anfragen. Nächster Schritt: Discovery-Workshop.",
    "payload": {"typ": "strategy", "kind": "ankunftspunkt", "entitaet": None, "segment": "gf-clevel"},
}
ALL_REPR = [PORTFOLIO_OM, PORTFOLIO_ADO, POS_CORE_OM, POS_DO, ANK_DAVID, ANK_ADO, ANK_OM_SITE, ANK_LANDING]

REAL_EXPORT = "/Users/davidobladen/NAS/DO-NAS-P/Coding/2026/obladen-strategy-brain/ssot/build/strategy_knowledge.jsonl"


# --- Routing ---
def test_route_portfolio_and_positionierung_by_entitaet():
    assert si.route_record(PORTFOLIO_OM) == "om"
    assert si.route_record(PORTFOLIO_ADO) == "ado"
    assert si.route_record(POS_CORE_OM) == "om"
    assert si.route_record(POS_DO) == "do"


@pytest.mark.parametrize("rec,org", [
    (ANK_DAVID, "do"),
    (ANK_ADO, "ado"),
    (ANK_OM_SITE, "om"),
    (ANK_LANDING, "om"),
])
def test_route_ankunftspunkt_by_target_site(rec, org):
    assert si.route_record(rec) == org


def test_route_unroutable_raises():
    bad = {"text": "x", "payload": {"typ": "strategy", "kind": "portfolio", "entitaet": None}}
    with pytest.raises(ValueError):
        si.route_record(bad)


# --- Parsing ---
def test_parse_ankunftspunkt_extracts_target_segment_offering():
    assert si.parse_ankunftspunkt(ANK_DAVID["text"]) == ("davidobladen.de", "gf-clevel", "ki-fuehrungskraefte")
    assert si.parse_ankunftspunkt(ANK_LANDING["text"]) == ("landing:ki-agenten", "gf-clevel", "agenten-orchestrierung")


def test_parse_ankunftspunkt_unparseable_raises():
    with pytest.raises(ValueError):
        si.parse_ankunftspunkt("kein ankunftspunkt-format")


# --- Logical-ID / Point-ID ---
def test_logical_id_shapes():
    assert si.logical_id(PORTFOLIO_OM) == "portfolio/ki-fuehrungskraefte"
    assert si.logical_id(POS_DO) == "positionierung/DO/DO"
    assert si.logical_id(ANK_ADO) == "ankunftspunkt/ado-site/kommunen/enablement"


def test_point_id_unique_and_deterministic():
    ids = [si.point_id(si.logical_id(r)) for r in ALL_REPR]
    assert len(ids) == len(set(ids))
    assert si.point_id("portfolio/x") == si.point_id("portfolio/x")
    assert si.point_id("portfolio/x") != si.point_id("portfolio/y")


# --- Payload ---
def test_build_payload_merges_contract_and_meta():
    p = si.build_payload(PORTFOLIO_OM, "om", "portfolio/ki-fuehrungskraefte")
    assert p["org"] == "om"
    assert p["project"] == "obladen-strategy-brain"
    assert p["source_type"] == "strategy"
    assert p["source_path"] == "portfolio/ki-fuehrungskraefte"
    assert p["git_remote"] is None
    assert p["chunk_text"] == PORTFOLIO_OM["text"]
    assert p["content_hash"] == si.content_hash(PORTFOLIO_OM["text"])
    # Strategie-Meta verbatim erhalten
    assert p["kind"] == "portfolio"
    assert p["entitaet"] == "OM"
    assert p["ref"] == "ki-fuehrungskraefte"


def test_build_payload_ankunftspunkt_adds_target_and_offering():
    p = si.build_payload(ANK_ADO, "ado", si.logical_id(ANK_ADO))
    assert p["entitaet"] is None
    assert p["segment"] == "kommunen"
    assert p["target"] == "ado-site"
    assert p["offering"] == "enablement"


# --- build_points ---
def test_build_points_groups_by_org():
    by_org = si.build_points(ALL_REPR)
    assert {o: len(v) for o, v in by_org.items()} == {"om": 4, "ado": 2, "do": 2}
    do_paths = {p["source_path"] for p in by_org["do"]}
    assert do_paths == {"positionierung/DO/DO", "ankunftspunkt/davidobladen.de/gf-clevel/ki-fuehrungskraefte"}


# --- Ingest-Flow (Qdrant + Embeddings gemockt) ---
def _mock_ctx(existing_by_org):
    """Gemockter client+emb; faengt upsert-source_paths ab. scroll liefert existing pro Collection."""
    emb = MagicMock()
    emb.embed_documents.side_effect = lambda texts: [[0.0] * 1536 for _ in texts]
    client = MagicMock()
    calls = {"upserted": [], "deleted": []}
    client.upsert.side_effect = lambda **k: calls["upserted"].extend(
        pt.payload["source_path"] for pt in k["points"])
    name_to_org = {v: kk for kk, v in si.COLLECTIONS.items()}

    def _scroll(collection_name, **k):
        org = name_to_org[collection_name]
        pts = []
        for sp, h in existing_by_org.get(org, {}).items():
            m = MagicMock()
            m.payload = {"source_path": sp, "content_hash": h}
            pts.append(m)
        return (pts, None)

    client.scroll.side_effect = _scroll
    return emb, client, calls


def _run(records, existing_by_org):
    emb, client, calls = _mock_ctx(existing_by_org)
    with patch.object(si, "get_client", return_value=client), \
         patch.object(si, "get_embeddings", return_value=emb), \
         patch.object(si, "ensure_collection"), \
         patch.object(si, "delete_by_source_path",
                      side_effect=lambda c, o, sp: calls["deleted"].append(sp)):
        summary = si.ingest(records)
    return summary, calls, emb


def test_ingest_dry_run_makes_no_client_calls():
    with patch.object(si, "get_client") as gc, patch.object(si, "get_embeddings") as ge:
        summary = si.ingest(ALL_REPR, dry_run=True)
    gc.assert_not_called()
    ge.assert_not_called()
    assert summary["dry_run"] is True
    assert summary["routed"] == {"om": 4, "ado": 2, "do": 2}


def test_ingest_first_run_embeds_all():
    summary, calls, emb = _run(ALL_REPR, existing_by_org={})
    assert emb.embed_documents.called
    assert summary["embedded"] == 8
    assert summary["skipped"] == 0
    assert calls["deleted"] == []
    assert len(calls["upserted"]) == 8


def test_ingest_unchanged_skips_no_embed():
    existing = {"om": {}, "ado": {}, "do": {}}
    for rec in ALL_REPR:
        existing[si.route_record(rec)][si.logical_id(rec)] = si.content_hash(rec["text"])
    summary, calls, emb = _run(ALL_REPR, existing_by_org=existing)
    emb.embed_documents.assert_not_called()
    assert summary["skipped"] == 8
    assert summary["embedded"] == 0
    assert calls["deleted"] == []


def test_ingest_orphan_pruned():
    existing = {"om": {"portfolio/GHOST": "deadbeef"}, "ado": {}, "do": {}}
    summary, calls, emb = _run(ALL_REPR, existing_by_org=existing)
    assert "portfolio/GHOST" in calls["deleted"]
    assert summary["deleted"] == 1


def test_existing_scope_filters_project_and_strategy_source_type():
    """Orphan-Prune darf NIE fremde Repo-Points anfassen, die denselben project-Wert
    tragen (source_type != strategy). Der scroll-Filter muss beides erzwingen."""
    client = MagicMock()
    client.scroll.return_value = ([], None)
    si._existing(client, "do")
    _, kwargs = client.scroll.call_args
    flt = str(kwargs["scroll_filter"])
    assert si.PROJECT in flt
    assert si.SOURCE_TYPE in flt


# --- Optionaler Full-Dataset-Check (nur lokal mit echtem Export) ---
@pytest.mark.skipif(not os.path.exists(REAL_EXPORT), reason="echtes strategy_knowledge.jsonl nicht vorhanden")
def test_full_dataset_routes_25_records_uniquely():
    records = si.load_jsonl(REAL_EXPORT)
    assert len(records) == 25
    by_org = si.build_points(records)
    assert {o: len(v) for o, v in by_org.items()} == {"om": 14, "ado": 7, "do": 4}
    ids = [p["id"] for v in by_org.values() for p in v]
    assert len(ids) == len(set(ids)) == 25
