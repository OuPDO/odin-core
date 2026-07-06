"""FastMCP-Server odin-knowledge. Registriert die Tools, startet stdio (lokal).

Aufruf:
    python -m odin_mcp.server
    python odin_mcp/server.py

HTTP-Remote-Transport (streamable-http mit API-Key) = SP-4.4.
"""
import os
import sys

# Macht den odin-core-Root importierbar bei Direktaufruf.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mcp.server.fastmcp import FastMCP

from odin_mcp import tools

# streamable_http_path="/" -> beim Mount unter "/mcp" wird der Endpoint sauber
# https://host/mcp (statt /mcp/mcp). Fuer stdio-Aufruf ist die Einstellung inert.
mcp = FastMCP("odin-knowledge", streamable_http_path="/")

for _fn in (tools.search_knowledge, tools.remember, tools.update_memory, tools.recall_about):
    mcp.tool()(_fn)


def main() -> None:
    """Startet den Server ueber stdio (Claude Code lokal)."""
    mcp.run()


if __name__ == "__main__":
    main()
