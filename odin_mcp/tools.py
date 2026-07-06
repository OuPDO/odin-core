"""Reine MCP-Tool-Funktionen. Delegieren an Memory-Store + Knowledge-Search.

Getrennt vom FastMCP-Server, damit die Logik ohne SDK-Interna testbar bleibt.
Cross-org: org ist Label/Filter, kein Zwang.
"""
from knowledge.search import knowledge_search
from memory import store


def search_knowledge(query: str, org: str | None = None) -> str:
    """Synthetisierte Antwort ueber Referenz-Wissen UND Memory (cross-org).

    Args:
        query: Natuerlichsprachige Frage.
        org: Optionaler Org-Filter (do/om/ado). None = alle.
    """
    return knowledge_search(query, org)


def remember(content: str, kind: str = "semantic", subject: str | None = None,
             key: str | None = None, org: str | None = None) -> dict:
    """Speichere einen Fakt/ein Erlebnis/eine Regel im Memory.

    key gesetzt = Upsert eines Profil-Werts; key null = Append. Gibt {id, action}.

    Args:
        content: Der zu merkende Inhalt.
        kind: semantic | episodic | procedural.
        subject: Worueber (z.B. "David", Person, Projekt).
        key: Praedikat fuer Upsert (z.B. "current_focus"); null = Append.
        org: do/om/ado/null (Label).
    """
    return store.remember(content, kind=kind, subject=subject, key=key, org=org,
                          provenance={"surface": "mcp"})


def update_memory(new_content: str, id: str | None = None, subject: str | None = None,
                  key: str | None = None, org: str | None = None) -> dict:
    """Ersetze ein bestehendes Memory durch neuen Inhalt (Supersede). Gibt {id, action}.

    Args:
        new_content: Der neue Inhalt.
        id: Direkte Memory-id, ODER
        subject/key/org: um das aktuell gueltige Memory zu finden.
    """
    return store.update_memory(new_content, id=id, subject=subject, key=key, org=org)


def recall_about(subject: str, kind: str | None = None) -> list[dict]:
    """Gib die aktuell gueltigen Memories ueber ein subject zurueck.

    Args:
        subject: Worueber.
        kind: Optionaler Typ-Filter (semantic | episodic | procedural).
    """
    return store.recall_about(subject, kind=kind)
