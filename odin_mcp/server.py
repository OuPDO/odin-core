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
from mcp.server.transport_security import TransportSecuritySettings

from odin_mcp import tools

# DNS-Rebinding-Schutz bleibt AN; hinter Traefik kommt der oeffentliche Host an,
# der explizit erlaubt werden muss (sonst 421 "Invalid Host header").
_ALLOWED_HOSTS = [
    "odin.oblm.de",
    "odin.168.119.183.251.sslip.io",
    "localhost",
    "127.0.0.1",
]

# streamable_http_path="/" -> beim Mount unter "/mcp" wird der Endpoint sauber
# https://host/mcp (statt /mcp/mcp). Fuer stdio-Aufruf sind die Einstellungen inert.
mcp = FastMCP(
    "odin-knowledge",
    streamable_http_path="/",
    transport_security=TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=_ALLOWED_HOSTS,
        allowed_origins=["https://odin.oblm.de"],
    ),
)

for _fn in (tools.search_knowledge, tools.remember, tools.update_memory, tools.recall_about):
    mcp.tool()(_fn)


def main() -> None:
    """Startet den Server ueber stdio (Claude Code lokal)."""
    mcp.run()


if __name__ == "__main__":
    main()
