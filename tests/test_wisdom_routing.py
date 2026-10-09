"""Routing: Gegencheck-Fragen gehen in den Wisdom-Speicher, nicht in die Projektwissen-Suche."""
import asyncio
from unittest.mock import MagicMock, patch

from agents import master, router


def _llm_returning(text):
    llm = MagicMock()
    llm.invoke.return_value = MagicMock(content=text)
    return llm


def test_classifier_accepts_wisdom_label():
    with patch.object(router, "get_azure_chat", return_value=_llm_returning("wisdom")):
        assert router.classify_intent("ist abo-preis fuer mein produkt eine gute idee?") == "wisdom"


def test_wisdom_is_a_known_intent_and_prompt_mentions_it():
    assert "wisdom" in router.INTENTS
    assert "wisdom" in router._PROMPT


def test_wisdom_intent_routes_to_wisdom_handler_not_knowledge():
    with patch.object(master, "classify_intent", return_value="wisdom"), \
         patch.object(master, "wisdom_answer", return_value="Fazit: anpassen.") as w, \
         patch.object(master, "knowledge_search", return_value="falsch") as k:
        out = asyncio.run(master.process_message("gegencheck: preise verdoppeln?", 1, 1))
    assert out == "Fazit: anpassen."
    w.assert_called_once()
    k.assert_not_called()


def test_knowledge_intent_still_routes_to_knowledge():
    with patch.object(master, "classify_intent", return_value="knowledge"), \
         patch.object(master, "knowledge_search", return_value="3 OM-Projekte aktiv."), \
         patch.object(master, "wisdom_answer", return_value="falsch") as w:
        out = asyncio.run(master.process_message("welche om projekte laufen", 1, 1))
    assert "OM-Projekte" in out
    w.assert_not_called()


def test_route_to_org_accepts_wisdom():
    assert master.route_to_org({"detected_org": "wisdom"}) == "wisdom"
    assert master.route_to_org({"detected_org": "unbekannt"}) == "master"
