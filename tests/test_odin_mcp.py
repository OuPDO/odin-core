from unittest.mock import patch

import pytest

import odin_mcp.tools as tools


def test_search_knowledge_delegates():
    with patch.object(tools, "knowledge_search", return_value="antwort") as ks:
        out = tools.search_knowledge("frage", org="do")
    assert out == "antwort"
    ks.assert_called_once_with("frage", "do")


def test_remember_delegates_with_mcp_provenance():
    with patch.object(tools.store, "remember", return_value={"id": "mem-1", "action": "insert"}) as r:
        out = tools.remember("fakt", kind="semantic", subject="David", key="focus", org="do")
    assert out == {"id": "mem-1", "action": "insert"}
    kwargs = r.call_args.kwargs
    assert kwargs["kind"] == "semantic"
    assert kwargs["subject"] == "David"
    assert kwargs["key"] == "focus"
    assert kwargs["org"] == "do"
    assert kwargs["provenance"] == {"surface": "mcp"}


def test_update_memory_delegates():
    with patch.object(tools.store, "update_memory", return_value={"id": "mem-2", "action": "supersede"}) as u:
        out = tools.update_memory("neu", id="mem-2", subject="David", key="focus", org="do")
    assert out == {"id": "mem-2", "action": "supersede"}
    assert u.call_args.kwargs["id"] == "mem-2"
    assert u.call_args.kwargs["subject"] == "David"
    assert u.call_args.kwargs["key"] == "focus"
    assert u.call_args.kwargs["org"] == "do"


def test_recall_about_delegates():
    with patch.object(tools.store, "recall_about", return_value=[{"id": "mem-1"}]) as rc:
        out = tools.recall_about("David", kind="semantic")
    assert out == [{"id": "mem-1"}]
    assert rc.call_args.kwargs["kind"] == "semantic"
    assert rc.call_args.args[0] == "David"


def test_remember_invalid_kind_raises_at_tool_boundary():
    # kein Store-Patch: die echte store.remember-Validierung muss ValueError werfen,
    # bevor irgendein DB-Call passiert.
    with pytest.raises(ValueError):
        tools.remember("x", kind="bogus")


@pytest.mark.asyncio
async def test_server_registers_all_tools():
    import odin_mcp.server as server
    registered = await server.mcp.list_tools()
    names = {t.name for t in registered}
    assert len(registered) == 6
    assert names == {"search_knowledge", "remember", "update_memory", "recall_about",
                     "wisdom_gegencheck", "wisdom_search"}
    # main ist aufrufbar (kein Start hier)
    assert callable(server.main)


def test_wisdom_gegencheck_delegates_with_theme():
    with patch.object(tools, "wisdom_answer", return_value="Fazit") as w:
        assert tools.wisdom_gegencheck("Preis verdoppeln?", theme="pricing") == "Fazit"
    w.assert_called_once_with("Preis verdoppeln?", theme="pricing")


def test_wisdom_search_returns_structured_hits_and_clamps_top_k():
    from unittest.mock import MagicMock
    h = MagicMock()
    h.score = 0.51234
    h.payload = {"source_path": "abc-q1", "lesson_en": "L", "lesson_de": "Ld", "answer_en": "Q",
                 "person": "Alex Hormozi", "theme": ["pricing"], "published": "2025-01-02",
                 "stance": "own", "source_url": "https://www.youtube.com/watch?v=abc&t=3s"}
    with patch.object(tools, "wisdom_hits", return_value=[h]) as wh:
        out = tools.wisdom_search("preis", theme="pricing", top_k=500)
    assert wh.call_args.kwargs["top_k"] == 20
    assert out == [{"id": "abc-q1", "score": 0.5123, "lesson_en": "L", "lesson_de": "Ld", "quote_en": "Q",
                    "person": "Alex Hormozi", "theme": ["pricing"], "published": "2025-01-02",
                    "stance": "own", "source_url": "https://www.youtube.com/watch?v=abc&t=3s"}]

