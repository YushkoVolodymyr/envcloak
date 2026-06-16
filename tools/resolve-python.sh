#!/bin/sh
# envcloak: locate a working Python 3 interpreter and bake it into Claude Code
# settings so the MCP server and PreToolUse hook can resolve it.
#
# Wired as a SessionStart hook. Runs on Linux, macOS, and Windows under any
# POSIX shell (Git Bash / MSYS2 included), so a single hook entry covers every
# platform that has a POSIX shell -- avoiding the per-session "command not
# found" noise that a separate PowerShell hook entry would inflict on unix
# users. Native Windows with no POSIX shell uses tools/resolve-python.ps1
# manually instead (see README).
#
# This shim's only job is to find ANY interpreter that runs as Python >= 3.8
# (verifying by EXECUTING it -- PATH presence is not enough; e.g. the Windows
# Store "python3" alias resolves but exits non-zero without running). It then
# hands off to resolve_interpreter.py, which runs under that interpreter and
# records its canonical sys.executable. Always exits 0: never block a session.

ROOT="${CLAUDE_PLUGIN_ROOT:-$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)}"
BRAIN="$ROOT/tools/resolve_interpreter.py"

# Succeeds iff the given interpreter command runs as Python >= 3.8.
try_run() {
  "$@" -c 'import sys; sys.exit(0 if sys.version_info[:2] >= (3, 8) else 1)' >/dev/null 2>&1
}

# Run the brain under the chosen interpreter; never let it fail the session.
# Suppress stdout: SessionStart hook stdout is injected into the model's
# context, and the resolved path there would be noise. stderr -> debug log.
handoff() {
  "$@" "$BRAIN" >/dev/null || true
  exit 0
}

# 1) Honour an existing ENVCLOAK_PYTHON override (re-bake to normalise it).
if [ -n "${ENVCLOAK_PYTHON:-}" ] && try_run "$ENVCLOAK_PYTHON"; then
  handoff "$ENVCLOAK_PYTHON"
fi

# 2) Common interpreter names, most-specific first.
for c in python3 python python3.13 python3.12 python3.11 python3.10 python3.9 python3.8; do
  if command -v "$c" >/dev/null 2>&1 && try_run "$c"; then
    handoff "$c"
  fi
done

# 3) Windows "py" launcher (available under Git Bash).
if command -v py >/dev/null 2>&1 && try_run py -3; then
  handoff py -3
fi

echo "[envcloak] no working Python 3.8+ found (tried python3, python, python3.x, py -3)." >&2
echo "[envcloak] set ENVCLOAK_PYTHON to your interpreter and restart Claude Code." >&2
exit 0
