---
name: web-research-model-policy
description: "Trigger: web-research-mcp, check_reference, save_research, cache miss, resolve_reference. Delegate WebSearch/WebFetch+summarize to a Haiku 4.5 subagent instead of the main thread's model."
license: Apache-2.0
metadata:
  author: jcsoftdev
  version: "1.0"
---

## Activation Contract

Load whenever the `web-research` MCP is connected and `check_reference`/`search_reference`/`resolve_reference` returns a miss or stale result — i.e. about to run `WebSearch`/`WebFetch` to populate `save_research`.

## Hard Rules

- Never run the cache-miss WebSearch/WebFetch + summarize step on the main thread's model.
- Delegate it to the `web-researcher` subagent (`~/.claude/agents/web-researcher.md`, `model: haiku` pinned in its own frontmatter) via the `Agent` tool.
- Only escalate past `web-researcher` when the research itself needs multi-step reasoning across sources, not a single-query lookup.
- The subagent must call `save_research(tech, topic, summary, content, ...)` itself before returning — don't round-trip raw results back to the main thread just to save them.

## Decision Gates

| Situation | Delegate to |
|---|---|
| Single query/URL, extract + summarize | `web-researcher` subagent (Haiku 4.5) |
| Multi-source synthesis needed for one topic | Sonnet (default session model) |
| Deep comparative/architectural research explicitly requested | Whatever model the user is already running |

## Execution Steps

1. On cache miss, delegate: `Agent({ subagent_type: "web-researcher", prompt: "<tech>/<topic>: <query or URL>" })`.
2. Let the subagent handle search, summary, and the `save_research` call end-to-end — its model is fixed in its own definition, no `model` override needed.
3. Report back only the slug/summary the subagent returns — don't re-summarize in the main thread.

## Output Contract

Confirm which model ran the search (Haiku 4.5 unless a Decision Gate escalation applied) and that `save_research` was called.
