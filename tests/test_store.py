import sqlite3

import pytest

from web_research_mcp.core.store import connect

REQUIRED = (
    "slug",
    "tech",
    "version",
    "topic",
    "summary",
    "content",
    "status_tag",
    "sources",
    "tags",
    "version_locked",
    "is_latest",
    "superseded_by",
    "ttl_days",
    "created_at",
    "updated_at",
)


def _insert(conn, **over):
    row = dict(
        slug="react/19/server-components",
        tech="react",
        version="19",
        topic="server-components",
        summary="rsc summary",
        content="server components let you render on the server",
        status_tag="current",
        sources=None,
        tags=None,
        version_locked=0,
        is_latest=1,
        superseded_by=None,
        ttl_days=30,
        created_at="2026-07-10T12:00:00+00:00",
        updated_at="2026-07-10T12:00:00+00:00",
    )
    row.update(over)
    cols = ", ".join(row)
    ph = ", ".join(f":{c}" for c in row)
    cur = conn.execute(f"INSERT INTO research_entries ({cols}) VALUES ({ph})", row)
    conn.commit()
    return cur.lastrowid


def _fts_rowids(conn, term):
    return [
        r[0]
        for r in conn.execute(
            "SELECT rowid FROM research_fts WHERE research_fts MATCH ?", (term,)
        ).fetchall()
    ]


def test_connect_row_factory_and_schema():
    conn = connect(":memory:")
    assert conn.row_factory is sqlite3.Row
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(research_entries)")}
    for c in REQUIRED:
        assert c in cols


def test_foreign_keys_on():
    conn = connect(":memory:")
    assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1


def test_wal_mode_on_file_db(tmp_path):
    conn = connect(str(tmp_path / "sub" / "research.db"))
    assert conn.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"


def test_slug_is_unique():
    conn = connect(":memory:")
    _insert(conn)
    with pytest.raises(sqlite3.IntegrityError):
        _insert(conn)


def test_insert_trigger_populates_fts():
    conn = connect(":memory:")
    rowid = _insert(conn)
    assert _fts_rowids(conn, "render") == [rowid]


def test_update_trigger_resyncs_fts():
    conn = connect(":memory:")
    # "render" is unique to the content column (not in topic/summary/tags).
    rowid = _insert(conn)
    conn.execute(
        "UPDATE research_entries SET content = ? WHERE id = ?",
        ("hooks let you use state in function components", rowid),
    )
    conn.commit()
    assert _fts_rowids(conn, "render") == []  # old content term gone
    assert _fts_rowids(conn, "hooks") == [rowid]  # new term indexed


def test_delete_trigger_removes_fts_row():
    conn = connect(":memory:")
    rowid = _insert(conn)
    conn.execute("DELETE FROM research_entries WHERE id = ?", (rowid,))
    conn.commit()
    assert _fts_rowids(conn, "render") == []
