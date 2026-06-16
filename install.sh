#!/usr/bin/env bash
# envcloak installer — full setup for a teammate's Claude Code (user scope).
# Path-agnostic: works from wherever this folder lives.
#
# It will:
#   1. run the test suite
#   2. register the envcloak MCP server (user scope)
#   3. install the PreToolUse hook into ~/.claude/hooks/
#   4. patch ~/.claude/settings.json (idempotently): deny all real .env files,
#      allow ONLY the envcloak MCP server, and wire the hook
set -euo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SERVER="$DIR/server.py"
HOOK_SRC="$DIR/block-env-files.py"

CLAUDE_DIR="${CLAUDE_CONFIG_DIR:-$HOME/.claude}"
HOOKS_DIR="$CLAUDE_DIR/hooks"
SETTINGS="$CLAUDE_DIR/settings.json"

command -v python3 >/dev/null || { echo "python3 is required" >&2; exit 1; }
command -v claude  >/dev/null || { echo "the 'claude' CLI is required" >&2; exit 1; }

echo "1/4 Running tests…"
( cd "$DIR" && python3 -m unittest >/dev/null 2>&1 ) && echo "    tests OK" || {
  echo "    tests FAILED — aborting" >&2; exit 1; }

echo "2/4 Registering envcloak MCP server (user scope)…"
chmod +x "$SERVER"
claude mcp remove envcloak -s user >/dev/null 2>&1 || true
claude mcp add envcloak -s user -- python3 "$SERVER"

echo "3/4 Installing PreToolUse hook -> $HOOKS_DIR/block-env-files.py"
mkdir -p "$HOOKS_DIR"
cp "$HOOK_SRC" "$HOOKS_DIR/block-env-files.py"
chmod +x "$HOOKS_DIR/block-env-files.py"

echo "4/4 Updating $SETTINGS"
python3 "$DIR/configure-settings.py" "$SETTINGS"

cat <<'EOF'

Done. Restart Claude Code (or run /hooks) to load the changes.

Now active in every project:
  • envcloak MCP allowed (and ONLY this server runs without a prompt)
  • all real .env files blocked from Read / Edit / Write / Grep / Bash
  • read secrets via envcloak tools (env_read returns a masked, safe view)

Example/template files (.env.example, .env.sample, .env.template, .env.dist,
.env.schema) stay readable via the hook.

Optional — also block @-mentions of .env files (mentions inline file content
outside the tool gate). Add this to the "hooks" object in settings.json:

  "UserPromptSubmit": [
    { "hooks": [ { "type": "command",
      "command": "python3 \"${CLAUDE_CONFIG_DIR:-$HOME/.claude}/hooks/block-env-files.py\"" } ] }
  ]
EOF
