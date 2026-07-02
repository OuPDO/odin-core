import asyncio
from unittest.mock import patch
import agents.master as master


def test_plain_message_routes_to_knowledge():
    with patch.object(master, "classify_intent", return_value="knowledge"), \
         patch.object(master, "knowledge_search", return_value="3 OM-Projekte aktiv."):
        out = asyncio.run(master.process_message("welche om projekte laufen", 8428283452, 1))
    assert "OM-Projekte" in out
