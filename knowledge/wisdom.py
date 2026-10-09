"""Wisdom-Orakel: Gegencheck einer Idee/Frage gegen den Millionaers-Wissensspeicher.

Nur lesend. Quelle der Wahrheit ist das Projekt learn-from-millionaires; ODIN hat
hier eine Projektion (Collection `wisdom_knowledge`). Der Stil folgt
`learn-from-millionaires/engine/prompts/GEGENCHECK_STYLE.md`.
"""
import logging

from config.embeddings import get_embeddings
from config.llm import get_azure_chat
from knowledge.qdrant_store import get_client
from knowledge.wisdom_store import search_wisdom_points

logger = logging.getLogger("odin.knowledge.wisdom")

DEFAULT_TOP_K = 8
UNDATED = "undatiert"

_STYLE = """Du bist ODINs Gegencheck-Sparringspartner ueber dem Millionaers-Wissensspeicher.
Pruefe die Idee/Frage ausschliesslich anhand der folgenden Treffer. Erfinde keine
Zitate, Zahlen, Namen oder Links und nutze kein Wissen ueber die Sprecher von aussen.
Jede Zuschreibung traegt den Quelllink aus dem Treffer. Ist die Treffermenge duenn
oder nur lose passend, sage das offen, statt mit allgemeinen Ratschlaegen zu fuellen.

Wichtig zur Beleglage: "Prinzip" ist eine Interpretation, belegt ist nur das Zitat
(O-Ton). Behaupte Zahlen oder Fakten nur, wenn sie im Zitat stehen. Das Datum ist das
Upload-Datum des Videos; die Aussage kann aelter sein. "undatiert" heisst unbekannt.

Antworte auf Deutsch in genau vier Abschnitten:
1. Ausrichtung: wo die Idee zu belegten Prinzipien passt
2. Abweichung und Risiko: wo sie dagegen laeuft (direkt, mit Quelle)
3. Blind Spots: was die Sprecher zusaetzlich fragen wuerden
4. Fazit: auf dem richtigen Weg, anpassen oder neu denken, plus der wichtigste naechste Schritt
Scharfer Berater, kein Cheerleader. Widersprechen sich Treffer, benenne die Spannung.
"""


def wisdom_hits(query: str, theme: str | None = None, person: str | None = None,
                top_k: int = DEFAULT_TOP_K) -> list:
    """Embed die Frage und suche in wisdom_knowledge. Degradiert bei Fehlern zu []."""
    try:
        vec = get_embeddings().embed_query(query)
    except Exception as exc:  # noqa: BLE001 - Orakel degradiert, bricht nie ab
        logger.warning("wisdom_hits: Embedding fehlgeschlagen: %s", exc)
        return []
    try:
        return search_wisdom_points(get_client(), vec, top_k=top_k, theme=theme, person=person)
    except Exception as exc:  # noqa: BLE001 - Orakel degradiert, bricht nie ab
        logger.warning("wisdom_hits: Qdrant-Suche fehlgeschlagen: %s", exc)
        return []


def format_hits(hits: list) -> str:
    if not hits:
        return "(keine Treffer)"
    blocks = []
    for n, h in enumerate(hits, start=1):
        p = h.payload or {}
        who = p.get("person") or "unbekannt"
        stance = p.get("stance")
        stance_txt = f" [Aussage: {stance}]" if stance and stance != "own" else ""
        blocks.append(
            f"[{n}] Prinzip (Interpretation): {p.get('lesson_de') or p.get('lesson_en') or ''}\n"
            f"    O-Ton (EN): {p.get('answer_en') or ''}\n"
            f"    Sprecher: {who}{stance_txt} | Datum: {p.get('published') or UNDATED} | "
            f"Themen: {', '.join(p.get('theme') or [])} | Quelle: {p.get('source_url')}"
        )
    return "\n".join(blocks)


def wisdom_answer(query: str, theme: str | None = None, person: str | None = None) -> str:
    hits = wisdom_hits(query, theme=theme, person=person)
    if not hits:
        return ("Im Wisdom-Speicher habe ich dazu keine belastbaren Treffer gefunden "
                "(oder die Suche ist gerade nicht erreichbar). Formuliere die Idee anders "
                "oder grenze ein Thema ein.")
    prompt = f"{_STYLE}\nTreffer:\n{format_hits(hits)}\n\nIdee/Frage: {query}\n\nAntwort:"
    return get_azure_chat().invoke(prompt).content.strip()
