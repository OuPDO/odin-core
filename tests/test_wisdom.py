"""Tests fuer wisdom_answer / format_hits / wisdom_store-Filter. Alles gemockt."""
from unittest.mock import MagicMock, patch

import knowledge.wisdom as w
import knowledge.wisdom_store as ws


def _hit(**p):
    h = MagicMock()
    h.payload = {"lesson_de": "Preise heben", "lesson_en": "Raise prices", "answer_en": "just do it",
                 "person": "Alex Hormozi", "theme": ["pricing"], "published": "2024-05-01",
                 "source_url": "https://www.youtube.com/watch?v=abc&t=1s", "stance": "own", **p}
    return h


def test_format_hits_labels_lesson_as_interpretation_and_shows_date_and_source():
    out = w.format_hits([_hit()])
    assert "Prinzip (Interpretation): Preise heben" in out
    assert "O-Ton (EN): just do it" in out
    assert "Datum: 2024-05-01" in out
    assert "https://www.youtube.com/watch?v=abc&t=1s" in out


def test_undated_hits_say_so_and_non_own_stance_is_flagged():
    out = w.format_hits([_hit(published=None, stance="quoting")])
    assert "Datum: undatiert" in out
    assert "[Aussage: quoting]" in out


def test_empty_hits_message():
    assert w.format_hits([]) == "(keine Treffer)"


def test_answer_without_hits_does_not_call_llm():
    with patch.object(w, "wisdom_hits", return_value=[]), patch.object(w, "get_azure_chat") as chat:
        out = w.wisdom_answer("idee")
    assert "keine belastbaren Treffer" in out
    chat.assert_not_called()


def test_answer_prompt_contains_hits_and_source_true_rules():
    llm = MagicMock()
    llm.invoke.return_value = MagicMock(content="  Fazit  ")
    with patch.object(w, "wisdom_hits", return_value=[_hit()]), patch.object(w, "get_azure_chat", return_value=llm):
        out = w.wisdom_answer("soll ich Preise heben?")
    prompt = llm.invoke.call_args[0][0]
    assert out == "Fazit"
    assert "Erfinde keine" in prompt and "Preise heben" in prompt and "soll ich Preise heben?" in prompt


def test_hits_degrade_to_empty_on_embedding_error():
    with patch.object(w, "get_embeddings", side_effect=RuntimeError("azure down")):
        assert w.wisdom_hits("x") == []


def test_hits_degrade_to_empty_on_qdrant_error():
    with patch.object(w, "get_embeddings", return_value=MagicMock(embed_query=lambda q: [0.0])), \
         patch.object(w, "get_client", return_value=MagicMock()), \
         patch.object(w, "search_wisdom_points", side_effect=RuntimeError("qdrant down")):
        assert w.wisdom_hits("x") == []


def test_search_builds_filters_only_for_given_arguments():
    client = MagicMock()
    ws.search_wisdom_points(client, [0.0], theme="pricing", person="Alex Hormozi")
    flt = client.query_points.call_args.kwargs["query_filter"]
    assert {c.key for c in flt.must} == {"theme", "person"}
    ws.search_wisdom_points(client, [0.0])
    assert client.query_points.call_args.kwargs["query_filter"] is None
    assert client.query_points.call_args.kwargs["collection_name"] == "wisdom_knowledge"
