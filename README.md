# web-research-mcp

An MCP server that keeps a **persistent, version-aware cache of web research** so
the host model (Claude, Codex, …) writes modern, non-deprecated code without
re-researching the same docs every session.

The server **never browses the web itself** — the host does the searching when the
user asks. This server only stores what was found, answers *"do we already have
this? is it current?"* cheaply, and serves the cached reference back.

## Install

One line — no clone needed:

```bash
curl -LsSf https://raw.githubusercontent.com/jcsoftdev/web-research-mcp/main/install.sh | bash
```

Or from a checkout:

```bash
./install.sh
```

Interactive: installs `uv` if missing, installs the `web-research-mcp` binary, then
asks which hosts to register into (Claude Code, Codex, Gemini, Claude Desktop,
Cursor) and wires each one up. The DB autocreates on first use.

### Manual registration

```bash
# Claude Code
claude mcp add web-research -s user -- web-research-mcp

# Codex
codex mcp add web-research -- web-research-mcp
```

Other hosts (JSON config):

```json
{
  "mcpServers": {
    "web-research": { "command": "web-research-mcp", "args": [] }
  }
}
```

## Tools

| Tool | Cost | Behavior |
|---|---|---|
| `list_tree(tech?)` | minimal | Hierarchy tech → version → topics (names + `is_latest`/`stale` flags, no content). |
| `check_reference(tech, topic, version?)` | low | `{status_tag, stale, exists, slug, is_latest, resolved_version}`. No content. Omit version → latest. |
| `check_reference_batch(items)` | low | Loops `check_reference` over `{tech, topic, version?}` items in one call. |
| `resolve_reference(tech, topic, version?, max_age_days?)` | low-high | check + fetch in one round-trip; `{exists: false}` on a miss. `max_age_days` overrides TTL for this call. |
| `stack_diff(items)` | low | Audits a full stack (`{tech, version?}`) against the cache: `fresh` \| `stale` \| `missing` per item. |
| `get_reference(slug, section?)` | med-high | Full markdown doc; optional section returns one heading block. |
| `search_reference(query, tech?)` | med | FTS5 over topic + summary + content + tags. |
| `save_research(...)` | write | Stores a doc; atomically supersedes older versions (PEP 440 compare); rejects redundant saves over a fresh entry unless `force=True`. |
| `invalidate_reference(slug)` | write | Forces a reference stale. |
| `cache_stats(limit?)` | minimal | Hit-rate, top missed techs (your research queue), estimated tokens saved by cache hits. |

Freshness is a **structured field** (`status_tag`, `stale`) placed first in every
response, and a stale entry carries an explicit `advice` field — the model can't
overlook deprecation buried in prose. A cache miss is a flat `{"exists": false}`.

### Topic canonicalization

Known alias spellings of the same recurring topic (`whats-new`,
`latest-changes`, `new-features`, ...) are folded into one canonical topic
(`latest-version`) before a slug is built or looked up — in `save_research`,
`check_reference`, `resolve_reference`, `check_reference_batch`, and
`get_reference` (which also accepts an alias slug directly). This is
**structural**, not advisory: two hosts spelling the same topic differently
land on the same cache entry instead of forking it. The alias map is a static
seed (`core/canonical.py`); DB-backed, runtime-taught aliases are a deliberate
v2.

### Dedup gate

`save_research` guards against forking the same concept under different topic
names (`server-components` vs `servercomponents`). Before inserting it looks for
similar existing topics for that tech and, if any, returns them in a
`possible_duplicates` field so the host reuses an existing slug instead of
creating a duplicate. It is **advisory, non-blocking** — unlike canonicalization,
above, it doesn't rewrite the topic, it only flags a candidate for the host to
reuse. Matching is lexical today (near-spellings, spacing, truncated
abbreviations); synonyms and non-truncation abbreviations (`rsc` vs
`server-components`) need embeddings, which swap in at the same call site via
`EmbeddingProvider` when `EMBEDDINGS_ENABLED=1`.

## Enforcement hook (optional)

The MCP `instructions` only *ask* the model to call `check_reference` before
writing code or searching the web — nothing enforces it, and an ephemeral
subagent picked via tool-search never even sees the server's `instructions`
(only each tool's own description). The installer can wire host hooks that
turn the ask into a guarantee, at two points:

```bash
web-research-mcp hook --host {claude|codex|gemini|cursor}
```

**Pre-edit gate** — if you are about to edit code for a cached tech and the
current session never consulted its reference, the edit is **denied** until
you do. Detects tracked techs via strong signals only (real JS/TS imports or
`package.json` dependency keys — never prose) and checks the session
transcript for a prior `check_reference` / `get_reference` call.

**Search-redundancy gate** (Claude Code only) — symmetric, for the other
direction: `WebSearch` / `WebFetch` is **denied** when the query/URL names a
tech that already has a *fresh* cached entry and it wasn't consulted this
session (call `resolve_reference` instead of re-researching). A `PostToolUse`
hook on the same tools injects a reminder to call `save_research` right after
a search completes — this fires for the main thread, `Task`-spawned
subagents, and Workflow `agent()` calls alike (all three verified empirically
to receive Claude Code hooks).

Detection is conservative by design in both gates: a false deny blocks
legitimate work.

Install is **opt-in** (default no) because a deny is disruptive. Support:

| host | event | status |
|---|---|---|
| Claude Code | `PreToolUse` `Edit\|Write` (deny, exit 2) + `PreToolUse`/`PostToolUse` `WebSearch\|WebFetch` | verified |
| Codex | `PreToolUse` (Bash-scoped — misses `apply_patch` edits) | experimental |
| Gemini CLI | `BeforeTool` | experimental (schema unverified) |
| Cursor | `beforeShellExecution` + `beforeMCPExecution` (no pre-edit block) | experimental |

The hook **fails open**: any parse error, unknown host, or unreadable DB allows
the action — a bug in the gate must never wedge your editor.

## Config (env vars)

| var | default | purpose |
|---|---|---|
| `WEB_RESEARCH_DB_PATH` | `~/.web-research-mcp/research.db` | DB location (global — reused across projects) |
| `DEFAULT_TTL_DAYS` | `30` | TTL for non-version-locked entries |
| `EMBEDDINGS_ENABLED` | `0` | vector search (post-MVP) |
| `WEB_RESEARCH_AUTO_UPDATE` | `1` | advertise updates so the host auto-delegates them; set `0` to disable |

### Auto-update

The server never installs anything itself. It exposes a `check_for_update` tool
and, via its MCP instructions, asks the host to **delegate a background agent** to
run the update when a newer version exists on GitHub — so the update never blocks
you and takes effect on the next launch. The host does the work; the server only
detects and advises.

With `WEB_RESEARCH_AUTO_UPDATE=0` the tool still exists but the instructions no
longer ask the host to auto-delegate — call `check_for_update` yourself when you
want it.

## Develop

```bash
uv sync
uv run pytest
```
