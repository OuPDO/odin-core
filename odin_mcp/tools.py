"""Reine MCP-Tool-Funktionen. Delegieren an Memory-Store + Knowledge-Search.

Getrennt vom FastMCP-Server, damit die Logik ohne SDK-Interna testbar bleibt.
Cross-org: org ist Label/Filter, kein Zwang.
"""
from knowledge.search import knowledge_search
from knowledge.wisdom import wisdom_answer, wisdom_hits
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


def wisdom_gegencheck(idea: str, theme: str | None = None) -> str:
    """Gegencheck einer unternehmerischen Idee/Entscheidung gegen den Millionaers-Wissensspeicher.

    Nur lesend. Liefert eine Antwort in vier Abschnitten (Ausrichtung, Abweichung und Risiko,
    Blind Spots, Fazit) mit Quelllink je Aussage. Lektionen sind Interpretation, belegt ist der
    O-Ton; das Datum ist das Upload-Datum.

    Args:
        idea: Die Idee, Entscheidung oder Frage (Deutsch oder Englisch).
        theme: Optional eines von pricing, sales, marketing, mindset, discipline-habits,
            hiring-team, leadership, wealth-building, investing, negotiation, starting-out,
            scaling, exit, failure-resilience.
    """
    return wisdom_answer(idea, theme=theme)


def wisdom_search(query: str, theme: str | None = None, person: str | None = None,
                  top_k: int = 8) -> list[dict]:
    """Strukturierte Treffer aus dem Wisdom-Speicher (ohne LLM-Synthese), nur lesend.

    Jeder Treffer: id, score, lesson_en/de (Interpretation), quote_en (O-Ton), person, theme,
    published (Upload-Datum oder null), stance, source_url (Deep-Link mit Zeitstempel).

    Args:
        query: Frage oder Stichworte.
        theme: Optionaler Themenfilter (siehe wisdom_gegencheck).
        person: Optionaler Personenfilter, z.B. "Alex Hormozi".
        top_k: Anzahl Treffer (1 bis 20).
    """
    hits = wisdom_hits(query, theme=theme, person=person, top_k=max(1, min(int(top_k), 20)))
    out = []
    for h in hits:
        p = h.payload or {}
        out.append({
            "id": p.get("source_path"), "score": round(float(getattr(h, "score", 0.0)), 4),
            "lesson_en": p.get("lesson_en"), "lesson_de": p.get("lesson_de"),
            "quote_en": p.get("answer_en"), "person": p.get("person"), "theme": p.get("theme"),
            "published": p.get("published"), "stance": p.get("stance"),
            "source_url": p.get("source_url"),
        })
    return out

