from unittest.mock import MagicMock, patch
import knowledge.search as ks


def test_knowledge_search_registry_only():
    rows = [{"name": "OMNIPULSE", "org": "om", "stack": "Laravel", "status": "active",
             "purpose_oneliner": "Central OM platform", "git_remote": "OuPDO/OMNIPULSE"}]
    llm = MagicMock()
    llm.invoke.return_value = MagicMock(content="Du hast 1 OM-Projekt: OMNIPULSE (aktiv).")
    with patch.object(ks, "query_projects", return_value=rows), \
         patch.object(ks, "get_azure_chat", return_value=llm), \
         patch.object(ks, "semantic_hits", return_value=[]), \
         patch.object(ks, "search_memory", return_value=[]):
        answer = ks.knowledge_search("welche OM-Projekte habe ich?", org="om")
    assert "OMNIPULSE" in answer
    assert llm.invoke.called


def test_knowledge_search_includes_semantic():
    rows = [{"name": "OMNIPULSE", "org": "om", "stack": "Laravel", "status": "active",
             "purpose_oneliner": "x", "git_remote": "y"}]
    hit = MagicMock()
    hit.payload = {"project": "OMNIPULSE", "chunk_text": "Filament admin panel"}
    hit.score = 0.9
    llm = MagicMock()
    llm.invoke.return_value = MagicMock(content="OMNIPULSE nutzt Filament.")
    with patch.object(ks, "query_projects", return_value=rows), \
         patch.object(ks, "get_azure_chat", return_value=llm), \
         patch.object(ks, "get_embeddings", return_value=MagicMock(embed_query=lambda q: [0.0])), \
         patch.object(ks, "get_client", return_value=MagicMock()), \
         patch.object(ks, "semantic_hits", return_value=[hit]), \
         patch.object(ks, "search_memory", return_value=[]):
        answer = ks.knowledge_search("womit ist OMNIPULSE gebaut?", org="om")
    prompt_arg = llm.invoke.call_args[0][0]
    assert "Filament admin panel" in prompt_arg
    assert "Filament" in answer


def test_unified_hits_returns_reference_and_memory():
    ref = MagicMock(); ref.payload = {"project": "OMNIPULSE", "chunk_text": "ref-chunk"}
    mem = MagicMock(); mem.payload = {"subject": "David", "kind": "semantic", "content": "mem-fact"}
    with patch.object(ks, "semantic_hits", return_value=[ref]), \
         patch.object(ks, "search_memory", return_value=[mem]):
        ref_hits, mem_hits = ks.unified_hits("frage", org="do")
    assert ref_hits == [ref]
    assert mem_hits == [mem]


def test_semantic_hits_degrades_on_embed_error():
    with patch.object(ks, "get_embeddings", side_effect=RuntimeError("azure down")):
        result = ks.semantic_hits("x", org="do")
    assert result == []


def test_knowledge_search_separates_reference_and_memory():
    rows = [{"name": "OMNIPULSE", "org": "om", "stack": "Laravel", "status": "active",
             "purpose_oneliner": "x", "git_remote": "y"}]
    ref = MagicMock(); ref.payload = {"project": "OMNIPULSE", "chunk_text": "Filament panel"}
    mem = MagicMock(); mem.payload = {"subject": "David", "kind": "semantic",
                                      "content": "David bevorzugt Voice-first"}
    llm = MagicMock()
    llm.invoke.return_value = MagicMock(content="Antwort mit beidem.")
    with patch.object(ks, "query_projects", return_value=rows), \
         patch.object(ks, "get_azure_chat", return_value=llm), \
         patch.object(ks, "semantic_hits", return_value=[ref]), \
         patch.object(ks, "search_memory", return_value=[mem]):
        answer = ks.knowledge_search("was praeferiert David?", org="do")
    prompt = llm.invoke.call_args[0][0]
    assert "Projektwissen" in prompt and "Filament panel" in prompt
    assert "Gemerktes" in prompt and "Voice-first" in prompt
    assert answer == "Antwort mit beidem."
