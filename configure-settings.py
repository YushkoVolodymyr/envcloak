#!/usr/bin/env python3
"""Idempotently merge envcloak protections into a Claude Code settings.json.

Usage: configure-settings.py <settings.json path> [hook_command]

Applies (adding only what is missing, preserving everything else):
  permissions.allow  += "mcp__envcloak"      -> allow ONLY this MCP server
  permissions.deny   += .env wildcards        -> block every real .env file
  hooks.PreToolUse   += block-env-files.py    -> deterministic enforcement

A `.bak` of the existing file is written before saving. Safe to re-run.
"""

import json
import os
import shutil
import sys

ALLOW = ["mcp__envcloak"]
DENY = [
    "Read(.env)", "Read(.env.*)", "Read(**/.env)", "Read(**/.env.*)",
    "Edit(.env)", "Edit(.env.*)", "Edit(**/.env)", "Edit(**/.env.*)",
    "Write(.env)", "Write(.env.*)", "Write(**/.env)", "Write(**/.env.*)",
    "Grep(**/.env*)",
]
MATCHER = "Read|Edit|Write|MultiEdit|NotebookEdit|Grep|Glob|Bash"
HOOK_MARKER = "block-env-files.py"
DEFAULT_HOOK_CMD = 'python3 "${CLAUDE_CONFIG_DIR:-$HOME/.claude}/hooks/block-env-files.py"'


def load(path: str) -> dict:
    if not os.path.exists(path):
        return {}
    with open(path, encoding="utf-8") as f:
        txt = f.read().strip()
    return json.loads(txt) if txt else {}


def add_missing(existing, items):
    out = list(existing)
    added = [it for it in items if it not in out]
    out.extend(added)
    return out, added


def main() -> None:
    if len(sys.argv) < 2:
        print("usage: configure-settings.py <settings.json> [hook_command]", file=sys.stderr)
        sys.exit(2)
    path = sys.argv[1]
    hook_cmd = sys.argv[2] if len(sys.argv) > 2 else DEFAULT_HOOK_CMD

    d = load(path)
    perms = d.setdefault("permissions", {})
    perms["allow"], allow_added = add_missing(perms.get("allow", []), ALLOW)
    perms["deny"], deny_added = add_missing(perms.get("deny", []), DENY)

    hooks = d.setdefault("hooks", {})
    pre = hooks.get("PreToolUse", [])

    def references_hook(entry: dict) -> bool:
        return any(HOOK_MARKER in (h.get("command", "") or "")
                   for h in entry.get("hooks", []))

    # Drop any prior wiring of our hook, then add a fresh entry (handles upgrades).
    pre = [e for e in pre if not references_hook(e)]
    pre.append({"matcher": MATCHER,
                "hooks": [{"type": "command", "command": hook_cmd}]})
    hooks["PreToolUse"] = pre

    if os.path.exists(path):
        shutil.copy2(path, path + ".bak")
    os.makedirs(os.path.dirname(os.path.abspath(path)) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(json.dumps(d, indent=2, ensure_ascii=False) + "\n")

    print(f"  permissions.allow += {allow_added or '(already present)'}")
    print(f"  permissions.deny  += {len(deny_added)} new entr"
          f"{'y' if len(deny_added) == 1 else 'ies'} "
          f"({'all already present' if not deny_added else ', '.join(deny_added)})")
    print(f"  hooks.PreToolUse  -> wired {HOOK_MARKER}")
    if os.path.exists(path + ".bak"):
        print(f"  backup written: {path}.bak")


if __name__ == "__main__":
    main()
