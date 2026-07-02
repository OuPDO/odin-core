from qdrant_client import QdrantClient
from qdrant_client.models import Distance, FieldCondition, Filter, MatchValue, VectorParams

from config.settings import settings

COLLECTIONS: dict[str, str] = {
    "om": "om_knowledge",
    "ado": "ado_knowledge",
    "do": "do_knowledge",
}


def _normalize_url(url: str) -> str:
    """Qdrant client defaults to :6333 when URL omits a port; proxied HTTPS endpoints
    (e.g. https://qdrant.oblm.de) serve on 443. Make the port explicit to avoid timeouts."""
    if url.startswith("https://") and ":" not in url.split("://", 1)[1]:
        return url + ":443"
    return url


def get_client() -> QdrantClient:
    return QdrantClient(url=_normalize_url(settings.qdrant_url), api_key=settings.qdrant_api_key, timeout=30)


def ensure_collection(client: QdrantClient, org: str, dim: int) -> None:
    """Dim-safe collection setup: create, no-op if same size, or delete+recreate on mismatch."""
    name = COLLECTIONS[org]
    if client.collection_exists(name):
        if client.get_collection(name).config.params.vectors.size == dim:
            return
        client.delete_collection(name)
    client.create_collection(name, vectors_config=VectorParams(size=dim, distance=Distance.COSINE))


def delete_by_repo(client, org: str, git_remote: str) -> None:
    """Loescht alle Punkte eines Repos (manueller Full-Prune / Notausgang).

    Nicht mehr Teil des naechtlichen Pfads -- der inkrementelle Ingest loescht
    per Datei via delete_by_source_path."""
    client.delete(
        collection_name=COLLECTIONS[org],
        points_selector=Filter(must=[FieldCondition(key="git_remote", match=MatchValue(value=git_remote))]),
    )


def delete_by_source_path(client, org: str, source_path: str) -> None:
    """Loescht alle Punkte einer logischen Datei (inkrementeller Re-Index)."""
    client.delete(
        collection_name=COLLECTIONS[org],
        points_selector=Filter(must=[FieldCondition(key="source_path", match=MatchValue(value=source_path))]),
    )


def existing_hashes(client, org: str, git_remote: str, page: int = 256) -> dict[str, str]:
    """Map source_path -> content_hash fuer alle bereits indexierten Points eines Repos.

    Liest die Payloads via paginiertem scroll (ohne Vektoren). Legacy-Points ohne
    content_hash werden auf "" gemappt, sodass jeder echte Hash mismatched -> Re-Embed.
    """
    out: dict[str, str] = {}
    flt = Filter(must=[FieldCondition(key="git_remote", match=MatchValue(value=git_remote))])
    offset = None
    while True:
        points, offset = client.scroll(
            collection_name=COLLECTIONS[org],
            scroll_filter=flt,
            with_payload=["source_path", "content_hash"],
            with_vectors=False,
            limit=page,
            offset=offset,
        )
        for p in points:
            payload = p.payload or {}
            sp = payload.get("source_path")
            if sp is None:
                continue
            out[sp] = payload.get("content_hash") or ""
        if offset is None:
            break
    return out


def search(client: QdrantClient, org: str, query_vector: list[float], top_k: int = 5) -> list:
    """Vector search gegen org-Collection. Gibt Punkte mit .payload und .score zurueck."""
    return client.query_points(
        collection_name=COLLECTIONS[org],
        query=query_vector,
        limit=top_k,
        with_payload=True,
    ).points
