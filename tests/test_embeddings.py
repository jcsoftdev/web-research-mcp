from web_research_mcp.core.embeddings import EmbeddingProvider, NullProvider


def test_null_provider_is_disabled():
    assert NullProvider().enabled is False


def test_null_provider_embed_returns_none():
    assert NullProvider().embed("anything") is None


def test_null_provider_is_an_embedding_provider():
    assert isinstance(NullProvider(), EmbeddingProvider)
