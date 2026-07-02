import pytest

from config.embeddings import get_embeddings
from config.settings import settings


def test_embeddings_factory_uses_deployment():
    emb = get_embeddings()
    assert emb.deployment == "text-embedding-3-small"


@pytest.mark.integration
def test_embeddings_live_dim():
    vec = get_embeddings().embed_query("hallo welt")
    assert len(vec) == settings.azure_embedding_dim
