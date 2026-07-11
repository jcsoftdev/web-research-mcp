"""Runtime configuration from environment variables."""

from __future__ import annotations

import os
from dataclasses import dataclass

DEFAULT_DB_PATH = "~/.web-research-mcp/research.db"
DEFAULT_TTL_DAYS = 30


@dataclass(frozen=True)
class Config:
    db_path: str
    default_ttl_days: int
    embeddings_enabled: bool
    auto_update: bool


def load_config() -> Config:
    """Read config from the environment, applying defaults."""
    raw_path = os.environ.get("WEB_RESEARCH_DB_PATH", DEFAULT_DB_PATH)
    db_path = os.path.abspath(os.path.expanduser(raw_path))
    ttl = int(os.environ.get("DEFAULT_TTL_DAYS", DEFAULT_TTL_DAYS))
    embeddings = os.environ.get("EMBEDDINGS_ENABLED", "0") == "1"
    auto_update = os.environ.get("WEB_RESEARCH_AUTO_UPDATE", "1") != "0"
    return Config(
        db_path=db_path,
        default_ttl_days=ttl,
        embeddings_enabled=embeddings,
        auto_update=auto_update,
    )
