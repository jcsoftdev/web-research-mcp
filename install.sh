#!/usr/bin/env bash
#
# web-research-mcp installer.
#
# Interactive. Steps:
#   1. Ensure `uv` is present (installs it if missing).
#   2. Install the `web-research-mcp` binary globally with `uv tool install`.
#   3. Ask which env config you want (DB path / TTL / embeddings) — Enter = default.
#   4. Ask which hosts to register into (Claude Code, Codex, Gemini,
#      Claude Desktop, Cursor) and wire each one up.
#   5. (optional) Install the enforcement hook.
#   6. Claude Code only: install the web-research-model-policy skill and the
#      web-researcher subagent (~/.claude/skills, ~/.claude/agents) so
#      cache-miss research always delegates to Haiku 4.5.
#   7. Verify the binary boots.
#
# Run it from the repo:   ./install.sh
# Or from anywhere against a checkout:   WEB_RESEARCH_SRC=/path/to/repo ./install.sh
#
set -euo pipefail

# macOS ships bash 3.2, which chokes on empty-array expansion under `set -u`.
# Re-exec under a modern bash if one is available (only when $0 is a real file;
# under `curl | bash` it isn't, and the empty-array guards below cover 3.2).
if [ "${BASH_VERSINFO:-0}" -lt 4 ] && [ -f "$0" ]; then
  for b in /opt/homebrew/bin/bash /usr/local/bin/bash; do
    if [ -x "$b" ]; then exec "$b" "$0" "$@"; fi
  done
fi

SERVER_NAME="web-research"
BIN_NAME="web-research-mcp"
LOCAL_BIN="${XDG_BIN_HOME:-$HOME/.local/bin}"
REPO_URL="${WEB_RESEARCH_REPO:-https://github.com/jcsoftdev/web-research-mcp.git}"

# ---- pretty output -----------------------------------------------------------
if [ -t 1 ]; then
  BOLD=$(printf '\033[1m'); DIM=$(printf '\033[2m'); RED=$(printf '\033[31m')
  GRN=$(printf '\033[32m'); YEL=$(printf '\033[33m'); CYN=$(printf '\033[36m')
  RST=$(printf '\033[0m')
else
  BOLD=""; DIM=""; RED=""; GRN=""; YEL=""; CYN=""; RST=""
fi
info() { printf '%s\n' "${CYN}▸${RST} $*"; }
ok()   { printf '%s\n' "${GRN}✓${RST} $*"; }
warn() { printf '%s\n' "${YEL}!${RST} $*" >&2; }
die()  { printf '%s\n' "${RED}✗${RST} $*" >&2; exit 1; }
# Can we actually open the terminal? `[ -r /dev/tty ]` lies (perms look fine
# even when there is no controlling tty), so probe with a real open.
if { true </dev/tty; } 2>/dev/null; then HAS_TTY=1; else HAS_TTY=0; fi

# Read from the terminal even when the script itself arrives via stdin
# (curl | bash). No tty -> fall back to the default.
ask()  {
  local p="$1" d="${2:-}" a=""
  local prompt; prompt="$(printf '%s' "${BOLD}${p}${RST}${d:+ ${DIM}[$d]${RST}} ")"
  if [ "$HAS_TTY" = 1 ]; then
    read -r -p "$prompt" a </dev/tty || true
  fi
  printf '%s' "${a:-$d}"
}

printf '\n%s\n\n' "${BOLD}web-research-mcp installer${RST}"

# ---- 1. ensure uv ------------------------------------------------------------
if ! command -v uv >/dev/null 2>&1; then
  info "uv not found — installing from astral.sh ..."
  curl -LsSf https://astral.sh/uv/install.sh | sh
  export PATH="$LOCAL_BIN:$HOME/.cargo/bin:$PATH"
  command -v uv >/dev/null 2>&1 || die "uv install failed; install it manually: https://docs.astral.sh/uv/"
fi
ok "uv $(uv --version | awk '{print $2}')"

# ---- 2. locate source + install ---------------------------------------------
# Prefer an explicit source, then the dir this script lives in (local run).
# When piped (curl | bash) neither exists, so clone the repo.
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]:-$0}")" 2>/dev/null && pwd || true)"
SRC="${WEB_RESEARCH_SRC:-$SCRIPT_DIR}"
if [ -z "$SRC" ] || [ ! -f "$SRC/pyproject.toml" ]; then
  command -v git >/dev/null 2>&1 || die "git is required to fetch the source; install git or run from a checkout"
  SRC="$(mktemp -d)/web-research-mcp"
  info "fetching source from $REPO_URL ..."
  git clone --depth 1 "$REPO_URL" "$SRC" >/dev/null 2>&1 || die "git clone failed: $REPO_URL"
fi
[ -f "$SRC/pyproject.toml" ] || die "no pyproject.toml at $SRC"

info "installing $BIN_NAME from $SRC ..."
uv tool install --from "$SRC" "$BIN_NAME" --force >/dev/null
BIN="$(command -v "$BIN_NAME" 2>/dev/null || printf '%s' "$LOCAL_BIN/$BIN_NAME")"
[ -x "$BIN" ] || die "binary not found after install (expected $BIN)"
case ":$PATH:" in *":$LOCAL_BIN:"*) : ;; *) warn "$LOCAL_BIN is not on your PATH — add it or run: uv tool update-shell" ;; esac
ok "installed: $BIN"

# ---- 3. env config -----------------------------------------------------------
printf '\n%s\n' "${BOLD}Config${RST} ${DIM}(Enter = default)${RST}"
DB_PATH="$(ask 'DB path' "$HOME/.web-research-mcp/research.db")"
TTL="$(ask 'Default TTL (days)' '30')"
EMB="$(ask 'Enable embeddings (post-MVP)? 0/1' '0')"

# Collect only non-default vars so the server keeps its own defaults otherwise.
ENV_KEYS=(); ENV_VALS=()
[ "$DB_PATH" != "$HOME/.web-research-mcp/research.db" ] && { ENV_KEYS+=("WEB_RESEARCH_DB_PATH"); ENV_VALS+=("$DB_PATH"); }
[ "$TTL" != "30" ] && { ENV_KEYS+=("DEFAULT_TTL_DAYS"); ENV_VALS+=("$TTL"); }
[ "$EMB" != "0" ]  && { ENV_KEYS+=("EMBEDDINGS_ENABLED"); ENV_VALS+=("$EMB"); }

# ---- 4. pick hosts -----------------------------------------------------------
declare -a H_LABEL H_KIND H_AVAIL
add_host() { H_LABEL+=("$1"); H_KIND+=("$2"); H_AVAIL+=("$3"); }

DESKTOP_CFG="$HOME/Library/Application Support/Claude/claude_desktop_config.json"
GEMINI_CFG="$HOME/.gemini/settings.json"
CURSOR_CFG="$HOME/.cursor/mcp.json"

command -v claude >/dev/null 2>&1 && add_host "Claude Code" "claude-cli" 1 || add_host "Claude Code" "claude-cli" 0
command -v codex  >/dev/null 2>&1 && add_host "Codex"       "codex-cli"  1 || add_host "Codex" "codex-cli" 0
{ [ -d "$HOME/.gemini" ] || command -v gemini >/dev/null 2>&1; } && add_host "Gemini" "json:$GEMINI_CFG" 1 || add_host "Gemini" "json:$GEMINI_CFG" 0
[ -d "$HOME/Library/Application Support/Claude" ] && add_host "Claude Desktop" "json:$DESKTOP_CFG" 1 || add_host "Claude Desktop" "json:$DESKTOP_CFG" 0
{ command -v cursor >/dev/null 2>&1 || [ -d "$HOME/.cursor" ]; } && add_host "Cursor" "json:$CURSOR_CFG" 1 || add_host "Cursor" "json:$CURSOR_CFG" 0

printf '\n%s\n' "${BOLD}Where do you want to register it?${RST}"
for i in "${!H_LABEL[@]}"; do
  mark=$([ "${H_AVAIL[$i]}" = 1 ] && printf '%s' "${GRN}detected${RST}" || printf '%s' "${DIM}not detected${RST}")
  printf '  %s) %-16s %s\n' "$((i+1))" "${H_LABEL[$i]}" "$mark"
done
printf '%s\n' "${DIM}Enter numbers (e.g. 1 2), 'all' for every detected host, or blank to skip.${RST}"
if [ "$HAS_TTY" = 1 ]; then
  SEL="$(ask 'Select' 'all')"
else
  SEL=""
  warn "no terminal available — skipping host registration (register later, see README)"
fi

SELECTED=()
if [ "$SEL" = "all" ]; then
  for i in "${!H_LABEL[@]}"; do [ "${H_AVAIL[$i]}" = 1 ] && SELECTED+=("$i"); done
elif [ -n "$SEL" ]; then
  for n in $SEL; do
    idx=$((n-1))
    [ "$idx" -ge 0 ] 2>/dev/null && [ "$idx" -lt "${#H_LABEL[@]}" ] && SELECTED+=("$idx") || warn "ignoring invalid choice: $n"
  done
fi
[ "${#SELECTED[@]}" -gt 0 ] || { warn "no hosts selected — binary is installed, register later with the commands in the README"; }

# ---- 5. register -------------------------------------------------------------
env_json() {
  python3 - "$@" <<'PY'
import json, sys
keys = sys.argv[1::2]; vals = sys.argv[2::2]
print(json.dumps(dict(zip(keys, vals))))
PY
}

json_merge() {  # json_merge <config_path> <bin> <env_json>
  python3 - "$1" "$2" "$3" "$SERVER_NAME" <<'PY'
import json, os, sys
path, binp, env_json, name = sys.argv[1:5]
os.makedirs(os.path.dirname(path), exist_ok=True)
data = {}
if os.path.exists(path):
    try:
        with open(path) as f:
            data = json.load(f) or {}
    except json.JSONDecodeError:
        raise SystemExit(f"existing config is not valid JSON: {path}")
servers = data.setdefault("mcpServers", {})
entry = {"command": binp, "args": []}
env = json.loads(env_json)
if env:
    entry["env"] = env
servers[name] = entry
with open(path, "w") as f:
    json.dump(data, f, indent=2)
    f.write("\n")
PY
}

# Space-separated env flags per host CLI (empty string when no custom vars).
CLAUDE_ENV=""; CODEX_ENV=""; ENVJSON="{}"
if [ "${#ENV_KEYS[@]}" -gt 0 ]; then
  args=()
  for i in "${!ENV_KEYS[@]}"; do
    CLAUDE_ENV="$CLAUDE_ENV -e ${ENV_KEYS[$i]}=${ENV_VALS[$i]}"
    CODEX_ENV="$CODEX_ENV --env ${ENV_KEYS[$i]}=${ENV_VALS[$i]}"
    args+=("${ENV_KEYS[$i]}" "${ENV_VALS[$i]}")
  done
  ENVJSON="$(env_json "${args[@]}")"
fi

printf '\n'
[ "${#SELECTED[@]}" -gt 0 ] || SELECTED=()
for idx in ${SELECTED[@]+"${SELECTED[@]}"}; do
  label="${H_LABEL[$idx]}"; kind="${H_KIND[$idx]}"
  case "$kind" in
    claude-cli)
      claude mcp remove "$SERVER_NAME" -s user >/dev/null 2>&1 || true
      # shellcheck disable=SC2086
      claude mcp add "$SERVER_NAME" -s user $CLAUDE_ENV -- "$BIN" >/dev/null \
        && ok "registered → $label" || warn "failed → $label"
      ;;
    codex-cli)
      codex mcp remove "$SERVER_NAME" >/dev/null 2>&1 || true
      # shellcheck disable=SC2086
      codex mcp add "$SERVER_NAME" $CODEX_ENV -- "$BIN" >/dev/null \
        && ok "registered → $label" || warn "failed → $label"
      ;;
    json:*)
      cfg="${kind#json:}"
      json_merge "$cfg" "$BIN" "$ENVJSON" \
        && ok "registered → $label ${DIM}($cfg)${RST}" || warn "failed → $label"
      ;;
  esac
done

# ---- 6. optional enforcement hook -------------------------------------------
# Blocks editing code for a cached tech until its reference was consulted this
# session. Opt-in (default no) because a deny is disruptive. Claude Code is
# fully supported and verified; the others use their documented 2026 hook
# schemas and are best-effort until validated against your host version.
hook_host_key() {  # map a host label to its hook adapter key ("" = no hooks)
  case "$1" in
    "Claude Code") printf 'claude' ;;
    "Codex")       printf 'codex'  ;;
    "Gemini")      printf 'gemini' ;;
    "Cursor")      printf 'cursor' ;;
    *)             printf '' ;;
  esac
}

hook_merge() {  # hook_merge <host_key> <bin>
  python3 - "$1" "$2" <<'PY'
import json, os, sys
host, binp = sys.argv[1], sys.argv[2]
cmd = f"{binp} hook --host {host}"
HOME = os.path.expanduser("~")
# (config path, layout, [(event, matcher), ...])
# claude gets both the pre-edit gate and the search-redundancy gate
# (pre + post on WebSearch/WebFetch) — its hook JSON schema is the only one
# verified end-to-end. Other hosts keep just the edit gate until validated.
SPECS = {
    "claude": (f"{HOME}/.claude/settings.json", "pretool", [
        ("PreToolUse", "Edit|Write"),
        ("PreToolUse", "WebSearch|WebFetch"),
        ("PostToolUse", "WebSearch|WebFetch"),
    ]),
    "codex":  (f"{HOME}/.codex/hooks.json",     "pretool", [("PreToolUse", "Bash")]),
    "gemini": (f"{HOME}/.gemini/settings.json", "pretool", [("BeforeTool", "*")]),
    "cursor": (f"{HOME}/.cursor/hooks.json",    "cursor",  None),
}
path, layout, specs = SPECS[host]
os.makedirs(os.path.dirname(path), exist_ok=True)
data = {}
if os.path.exists(path):
    try:
        with open(path) as f:
            data = json.load(f) or {}
    except json.JSONDecodeError:
        raise SystemExit(f"existing config is not valid JSON: {path}")

def has_cmd(blob):
    return cmd in json.dumps(blob)

if layout == "pretool":
    # Claude/Codex/Gemini share the matcher+hooks list shape under an events map.
    events = data.setdefault("hooks", {})
    for event, matcher in specs:
        entries = events.setdefault(event, [])
        exists = any(
            e.get("matcher") == matcher and has_cmd(e.get("hooks", []))
            for e in entries
        )
        if not exists:
            entries.append({"matcher": matcher,
                            "hooks": [{"type": "command", "command": cmd}]})
elif layout == "cursor":
    # Cursor cannot block a raw file write pre-hoc; guard the surfaces it can:
    # shell and MCP tool execution.
    data.setdefault("version", 1)
    hooks = data.setdefault("hooks", {})
    for ev in ("beforeShellExecution", "beforeMCPExecution"):
        lst = hooks.setdefault(ev, [])
        if not has_cmd(lst):
            lst.append({"command": cmd})

with open(path, "w") as f:
    json.dump(data, f, indent=2)
    f.write("\n")
print(path)
PY
}

if [ "${#SELECTED[@]}" -gt 0 ]; then
  printf '\n%s %s\n' "${BOLD}Enforcement hook${RST}" "${DIM}(optional)${RST}"
  printf '%s\n' "${DIM}Blocks editing code for a cached tech until you call check_reference.${RST}"
  printf '%s\n' "${DIM}Claude Code: verified. Codex/Gemini/Cursor: experimental (unverified schema).${RST}"
  HOOK="$(ask 'Install enforcement hook? y/N' 'N')"
  case "$HOOK" in
    y|Y|yes|YES)
      for idx in ${SELECTED[@]+"${SELECTED[@]}"}; do
        hk="$(hook_host_key "${H_LABEL[$idx]}")"
        [ -n "$hk" ] || { warn "no hook support → ${H_LABEL[$idx]} (skipped)"; continue; }
        p="$(hook_merge "$hk" "$BIN")" \
          && ok "hook → ${H_LABEL[$idx]} ${DIM}($p)${RST}" \
          || warn "hook failed → ${H_LABEL[$idx]}"
      done
      ;;
    *) info "skipped enforcement hook (register later, see README)" ;;
  esac
fi

# ---- 6b. Claude Code skill + subagent (cache-miss research → Haiku) ---------
# Non-disruptive (no denies, just guidance + an agent definition) — installed
# unconditionally for Claude Code, independent of the enforcement-hook opt-in.
install_claude_extras() {  # install_claude_extras <src_dir>
  local src="$1"
  local skills_dst="$HOME/.claude/skills/web-research-model-policy"
  local agents_dst="$HOME/.claude/agents"
  mkdir -p "$skills_dst" "$agents_dst"
  cp -f "$src/claude/skills/web-research-model-policy/SKILL.md" "$skills_dst/SKILL.md" \
    && cp -f "$src/claude/agents/web-researcher.md" "$agents_dst/web-researcher.md"
}

for idx in ${SELECTED[@]+"${SELECTED[@]}"}; do
  if [ "${H_LABEL[$idx]}" = "Claude Code" ]; then
    install_claude_extras "$SRC" \
      && ok "skill + subagent → Claude Code ${DIM}(cache-miss research delegates to Haiku)${RST}" \
      || warn "failed to install skill/subagent → Claude Code"
    break
  fi
done

# ---- 7. verify ---------------------------------------------------------------
printf '\n'
if printf '' | "$BIN" >/dev/null 2>&1; then
  ok "server boots"
else
  warn "server did not exit cleanly on empty stdin (may still be fine)"
fi

printf '\n%s\n' "${GRN}${BOLD}Done.${RST} DB autocreates at first use: ${DIM}${DB_PATH}${RST}"
printf '%s\n' "Restart your host (or reload MCP) to pick up ${SERVER_NAME}."
