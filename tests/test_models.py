from web_research_mcp.core.models import ResearchEntry


def _row(**over):
    base = dict(
        id=1,
        slug="react/19/server-components",
        tech="react",
        version="19",
        topic="server-components",
        summary="RSC summary",
        content="# full doc",
        status_tag="current",
        sources='["https://react.dev"]',
        tags='["rsc","react"]',
        version_locked=0,
        is_latest=1,
        superseded_by=None,
        ttl_days=30,
        created_at="2026-07-10T12:00:00+00:00",
        updated_at="2026-07-10T12:00:00+00:00",
    )
    base.update(over)
    return base


def test_from_row_parses_json_and_bools():
    entry = ResearchEntry.from_row(_row())
    assert entry.sources == ["https://react.dev"]
    assert entry.tags == ["rsc", "react"]
    assert entry.version_locked is False
    assert entry.is_latest is True


def test_from_row_handles_null_json():
    entry = ResearchEntry.from_row(_row(sources=None, tags=None, version=None))
    assert entry.sources == []
    assert entry.tags == []
    assert entry.version is None


def test_from_row_version_locked_true():
    entry = ResearchEntry.from_row(_row(version_locked=1, ttl_days=None))
    assert entry.version_locked is True
    assert entry.ttl_days is None
