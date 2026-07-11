"""Best-effort background self-update.

On startup the server checks GitHub for a newer released version and, if one
exists, reinstalls itself via ``uv tool install --force`` in the background.
The update takes effect on the next launch. Everything here is best-effort:
any network or install failure is swallowed so it never breaks the server.

Opt out with ``WEB_RESEARCH_AUTO_UPDATE=0``.
"""

from __future__ import annotations

import re
import subprocess
import sys
from typing import Callable

from .versioning import is_newer

RAW_VERSION_URL = (
    "https://raw.githubusercontent.com/jcsoftdev/web-research-mcp/main/"
    "src/web_research_mcp/__init__.py"
)
GIT_INSTALL_SPEC = "git+https://github.com/jcsoftdev/web-research-mcp.git"

_VERSION_RE = re.compile(r"""__version__\s*=\s*['"]([^'"]+)['"]""")


def parse_remote_version(text: str) -> str | None:
    """Pull ``__version__`` out of a fetched ``__init__.py``; None if absent."""
    m = _VERSION_RE.search(text)
    return m.group(1) if m else None


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


def _fetch_remote() -> str:
    import urllib.request

    with urllib.request.urlopen(RAW_VERSION_URL, timeout=5) as resp:
        return resp.read().decode("utf-8")


def _run_install() -> None:
    subprocess.run(
        ["uv", "tool", "install", "--from", GIT_INSTALL_SPEC, "web-research-mcp", "--force"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
        timeout=300,
    )


def maybe_self_update(
    current: str,
    fetch: Callable[[], str] = _fetch_remote,
    install: Callable[[], None] = _run_install,
    log: Callable[[str], None] | None = None,
) -> None:
    """Check for a newer version and, if found, reinstall in place.

    Runs the whole flow inside a try/except so a background thread calling this
    can never crash the server.
    """
    log = log or (lambda msg: print(msg, file=sys.stderr))
    try:
        newer = check_for_update(current, fetch)
        if not newer:
            return
        log(f"web-research-mcp: updating {current} -> {newer} in background ...")
        install()
        log(f"web-research-mcp: updated to {newer}; takes effect on next restart.")
    except Exception:
        pass
