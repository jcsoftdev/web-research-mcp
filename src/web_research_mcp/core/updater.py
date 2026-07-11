"""Update detection.

The server never installs anything itself. It only *detects* whether a newer
version exists on GitHub and reports it; the host is expected to delegate a
background agent to run the update command. Everything here is best-effort:
any network failure yields "no update".
"""

from __future__ import annotations

import re
from typing import Callable

from .versioning import is_newer

RAW_VERSION_URL = (
    "https://raw.githubusercontent.com/jcsoftdev/web-research-mcp/main/"
    "src/web_research_mcp/__init__.py"
)
GIT_INSTALL_SPEC = "git+https://github.com/jcsoftdev/web-research-mcp.git"

# The command the host's agent should run to update the server.
UPDATE_COMMAND = f"uv tool install --from {GIT_INSTALL_SPEC} web-research-mcp --force"

_VERSION_RE = re.compile(r"""__version__\s*=\s*['"]([^'"]+)['"]""")


def parse_remote_version(text: str) -> str | None:
    """Pull ``__version__`` out of a fetched ``__init__.py``; None if absent."""
    m = _VERSION_RE.search(text)
    return m.group(1) if m else None


def _fetch_remote() -> str:
    import urllib.request

    with urllib.request.urlopen(RAW_VERSION_URL, timeout=5) as resp:
        return resp.read().decode("utf-8")


def check_for_update(current: str, fetch: Callable[[], str]) -> str | None:
    """Return the remote version if it is newer than ``current``, else None.

    Best-effort: a failing ``fetch`` or unparseable body yields None.
    """
    try:
        remote = parse_remote_version(fetch())
    except Exception:
        return None
    if remote and is_newer(remote, current):
        return remote
    return None


def update_status(current: str, fetch: Callable[[], str] = _fetch_remote) -> dict:
    """Structured update report for the host to act on.

    When newer, includes the exact ``command`` a delegated agent should run.
    """
    latest = check_for_update(current, fetch)
    if latest:
        return {
            "update_available": True,
            "current": current,
            "latest": latest,
            "command": UPDATE_COMMAND,
        }
    return {"update_available": False, "current": current}
