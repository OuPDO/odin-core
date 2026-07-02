"""Knowledge search -- registry + semantic (Qdrant) combined context.

Phase A (A6): registry-only.
Phase B (B4): adds semantic_hits via Qdrant vector search.
"""
import logging

from config.embeddings import get_embeddings
from config.llm import get_azure_chat
from knowledge.qdrant_store import COLLECTIONS, get_client, search
from memory.registry import query_projects

logger = logging.getLogger("odin.knowledge.search")


def _format_rows(rows: list[dict]) -> str:
    if not rows:
        return "(keine Projekte gefunden)"
    lines = []
    for r in rows:
        lines.append(
            f"- {r['name']} [{r.get('org')}] stack={r.get('stack')} "
            f"status={r.get('status')} :: {r.get('purpose_oneliner') or ''}"
        )
    return "\n".join(lines)


def semantic_hits(query: str, org: str | None) -> list:
    """Embed query, search relevant org collection(s), return top 5 hits by score.

    Args:
        query: Natural-language question to embed.
        org: Optional org filter. If not in COLLECTIONS, searches all orgs.

    Returns:
        Up to 5 Qdrant ScoredPoint objects sorted by score descending.
    """
    vec = get_embeddings().embed_query(query)
    client = get_client()
    orgs = [org] if org in COLLECTIONS else list(COLLECTIONS)
    hits: list = []
    for o in orgs:
        try:
            hits += search(client, o, vec, top_k=5)
        except Exception as exc:
            logger.warning("Qdrant-Suche fehlgeschlagen fuer org %s: %s", o, exc)
            continue
    hits.sort(key=lambda h: getattr(h, "score", 0), reverse=True)
    return hits[:5]


def _format_hits(hits: list) -> str:
    if not hits:
        return "(keine semantischen Treffer)"
    return "\n".join(
        f"- [{h.payload.get('project')}] {h.payload.get('chunk_text', '')[:300]}"
        for h in hits
    )


def knowledge_search(query: str, org: str | None = None) -> str:
    """Return a natural-language answer about David's projects.

    Combines project registry rows with Qdrant semantic hits in the synthesis prompt.

    Args:
        query: Natural-language question.
        org: Optional org filter (om / ado / do). None = all orgs.

    Returns:
        Synthesised answer string.
    """
    rows = query_projects(org=org)
    hits = semantic_hits(query, org)
    prompt = (
        "Du bist ODIN, Davids Wissens-Assistent. Antworte praezise auf Basis von "
        "Projektliste UND Wissens-Auszuegen. Erfinde nichts.\n\n"
        f"Projektliste:\n{_format_rows(rows)}\n\n"
        f"Wissens-Auszuege:\n{_format_hits(hits)}\n\n"
        f"Frage: {query}\n\nAntwort:"
    )
    return get_azure_chat().invoke(prompt).content.strip()
