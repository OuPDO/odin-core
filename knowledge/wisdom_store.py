"""Qdrant-Zugriff fuer den Wisdom-Wissensspeicher (learn-from-millionaires).

Eigene Collection statt Vermischung mit om/ado/do_knowledge: ~6.300 atomare
Insights wuerden die wenigen Projekt-Chunks der generischen knowledge_search
ueberschwemmen, und die Payload (theme, person, published, source_url) braucht
eigene Filter.
"""
from qdrant_client import QdrantClient
from qdrant_client.models import (
    Distance,
    FieldCondition,
    Filter,
    MatchValue,
    VectorParams,
)

WISDOM_COLLECTION: str = "wisdom_knowledge"


def ensure_wisdom_collection(client: QdrantClient, dim: int) -> None:
    """Dim-safe Setup analog ensure_memory_collection: create, no-op, oder recreate."""
    if client.collection_exists(WISDOM_COLLECTION):
        if client.get_collection(WISDOM_COLLECTION).config.params.vectors.size == dim:
            return
        client.delete_collection(WISDOM_COLLECTION)
    client.create_collection(
        WISDOM_COLLECTION, vectors_config=VectorParams(size=dim, distance=Distance.COSINE)
    )


def existing_wisdom_hashes(client: QdrantClient, page: int = 256) -> dict[str, str]:
    """Map source_path (= Insight-ID) -> content_hash aller indexierten Wisdom-Points."""
    out: dict[str, str] = {}
    offset = None
    while True:
        points, offset = client.scroll(
            collection_name=WISDOM_COLLECTION,
            with_payload=["source_path", "content_hash"],
            with_vectors=False,
            limit=page,
            offset=offset,
        )
        for p in points:
            payload = p.payload or {}
            sp = payload.get("source_path")
            if sp:
                out[sp] = payload.get("content_hash") or ""
        if offset is None:
            break
    return out


def delete_wisdom_by_source_path(client: QdrantClient, source_path: str) -> None:
    client.delete(
        collection_name=WISDOM_COLLECTION,
        points_selector=Filter(must=[FieldCondition(key="source_path", match=MatchValue(value=source_path))]),
    )


def search_wisdom_points(
    client: QdrantClient,
    query_vector: list[float],
    top_k: int = 8,
    theme: str | None = None,
    person: str | None = None,
    channel: str | None = None,
) -> list:
    """Vector-Search gegen wisdom_knowledge mit optionalen Payload-Filtern (nur lesend)."""
    must = []
    if theme:
        must.append(FieldCondition(key="theme", match=MatchValue(value=theme)))
    if person:
        must.append(FieldCondition(key="person", match=MatchValue(value=person)))
    if channel:
        must.append(FieldCondition(key="channel", match=MatchValue(value=channel)))
    return client.query_points(
        collection_name=WISDOM_COLLECTION,
        query=query_vector,
        query_filter=Filter(must=must) if must else None,
        limit=top_k,
        with_payload=True,
    ).points
