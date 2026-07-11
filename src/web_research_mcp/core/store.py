"""SQLite connection, pragmas, schema, FTS5 and its sync triggers.

One global DB (see config). ``connect`` is idempotent: it applies the
per-connection pragmas and ensures the schema exists on every open.
"""

from __future__ import annotations

import os
import sqlite3

_PRAGMAS = (
    "PRAGMA journal_mode=WAL",
    "PRAGMA synchronous=NORMAL",
    "PRAGMA busy_timeout=5000",
    "PRAGMA foreign_keys=ON",
    "PRAGMA cache_size=-64000",
    "PRAGMA temp_store=MEMORY",
)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS research_entries (
    id             INTEGER PRIMARY KEY,
    slug           TEXT NOT NULL UNIQUE,
    tech           TEXT NOT NULL,
    version        TEXT,
    topic          TEXT NOT NULL,
    summary        TEXT NOT NULL,
    content        TEXT NOT NULL,
    status_tag     TEXT NOT NULL,
    sources        TEXT,
    tags           TEXT,
    version_locked INTEGER NOT NULL DEFAULT 0,
    is_latest      INTEGER NOT NULL DEFAULT 1,
    superseded_by  INTEGER REFERENCES research_entries(id),
    ttl_days       INTEGER,
    created_at     TEXT NOT NULL,
    updated_at     TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_tech_topic   ON research_entries(tech, topic);
CREATE INDEX IF NOT EXISTS idx_tech_version ON research_entries(tech, version);
CREATE INDEX IF NOT EXISTS idx_tech_latest  ON research_entries(tech, is_latest);

CREATE VIRTUAL TABLE IF NOT EXISTS research_fts USING fts5(
    topic, summary, content, tags,
    content='research_entries',
    content_rowid='id'
);

-- Keep the FTS index in sync. All three triggers are mandatory; a missing
-- DELETE trigger leaves orphaned FTS rows. UPDATE issues an FTS 'delete'
-- then reinsert (never a bare update) to avoid index corruption.
CREATE TRIGGER IF NOT EXISTS research_ai AFTER INSERT ON research_entries BEGIN
    INSERT INTO research_fts(rowid, topic, summary, content, tags)
    VALUES (new.id, new.topic, new.summary, new.content, new.tags);
END;

CREATE TRIGGER IF NOT EXISTS research_ad AFTER DELETE ON research_entries BEGIN
    INSERT INTO research_fts(research_fts, rowid, topic, summary, content, tags)
    VALUES ('delete', old.id, old.topic, old.summary, old.content, old.tags);
END;

CREATE TRIGGER IF NOT EXISTS research_au AFTER UPDATE ON research_entries BEGIN
    INSERT INTO research_fts(research_fts, rowid, topic, summary, content, tags)
    VALUES ('delete', old.id, old.topic, old.summary, old.content, old.tags);
    INSERT INTO research_fts(rowid, topic, summary, content, tags)
    VALUES (new.id, new.topic, new.summary, new.content, new.tags);
END;
"""


def connect(db_path: str) -> sqlite3.Connection:
    """Open (creating parent dirs) and return a configured connection."""
    if db_path != ":memory:":
        parent = os.path.dirname(db_path)
        if parent:
            os.makedirs(parent, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    for pragma in _PRAGMAS:
        conn.execute(pragma)
    conn.executescript(_SCHEMA)
    conn.commit()
    return conn
