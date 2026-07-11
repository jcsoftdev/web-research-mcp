import os

from web_research_mcp.config import load_config


def test_defaults(monkeypatch):
    for var in ("WEB_RESEARCH_DB_PATH", "DEFAULT_TTL_DAYS", "EMBEDDINGS_ENABLED"):
        monkeypatch.delenv(var, raising=False)
    cfg = load_config()
    assert cfg.db_path.endswith(".web-research-mcp/research.db")
    assert os.path.isabs(cfg.db_path)  # ~ expanded
    assert cfg.default_ttl_days == 30
    assert cfg.embeddings_enabled is False


def test_overrides(monkeypatch):
    monkeypatch.setenv("WEB_RESEARCH_DB_PATH", "/tmp/x/research.db")
    monkeypatch.setenv("DEFAULT_TTL_DAYS", "7")
    monkeypatch.setenv("EMBEDDINGS_ENABLED", "1")
    cfg = load_config()
    assert cfg.db_path == "/tmp/x/research.db"
    assert cfg.default_ttl_days == 7
    assert cfg.embeddings_enabled is True


def test_embeddings_zero_is_false(monkeypatch):
    monkeypatch.setenv("EMBEDDINGS_ENABLED", "0")
    assert load_config().embeddings_enabled is False
