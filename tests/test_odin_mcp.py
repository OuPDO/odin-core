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
async def test_server_registers_four_tools():
    import odin_mcp.server as server
    registered = await server.mcp.list_tools()
    names = {t.name for t in registered}
    assert len(registered) == 4
    assert names == {"search_knowledge", "remember", "update_memory", "recall_about"}
    # main ist aufrufbar (kein Start hier)
    assert callable(server.main)
