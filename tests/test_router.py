from unittest.mock import MagicMock, patch
import agents.router as router


def _llm_returning(text):
    llm = MagicMock()
    llm.invoke.return_value = MagicMock(content=text)
    return llm


def test_classify_knowledge():
    with patch.object(router, "get_azure_chat", return_value=_llm_returning("knowledge")):
        assert router.classify_intent("welche OM-Projekte habe ich gerade?") == "knowledge"


def test_classify_unknown_falls_back_to_chat():
    with patch.object(router, "get_azure_chat", return_value=_llm_returning("banana")):
        assert router.classify_intent("hi") == "chat"
