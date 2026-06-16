#!/usr/bin/env python3
"""Block access to real .env files across every Claude Code session.

Registered as a user-global hook so it applies regardless of which directory
Claude is launched from (settings.local.json deny rules only load for the exact
project root, which is easy to miss in a multi-project workspace).

Handles two events:
  * PreToolUse      -> deny Read/Edit/Write/Grep/Glob/Bash that targets a
                       protected .env file (covers subagent tool calls too).
  * UserPromptSubmit-> block a prompt that @-mentions a protected .env file
                       (mentions inline file content outside the tool gate).

Protected = basename `.env` or `.env.<suffix>`, EXCEPT example/template files
(`.env.example`, `.env.sample`, `.env.template`, `.env.dist`, `.env.schema`),
which carry no secrets and stay readable.

Secrets should be read through the envcloak MCP (`env_read`), which returns a
masked, length-preserving view instead of raw values.
"""

import json
import os
import re
import sys

SAFE_SUFFIX = re.compile(r"\.(example|sample|template|dist|schema)$", re.IGNORECASE)
# a .env token as it appears inside a bash command line
BASH_ENV_TOKEN = re.compile(r"""(?:^|[\s=:"'(<>|&;/])(\.env(?:\.[A-Za-z0-9_.-]+)?)""")
# an @-mention of a .env file in a user prompt
MENTION_ENV = re.compile(r"@(?:[^\s\"']*/)?(\.env(?:\.[A-Za-z0-9_.-]+)?)\b")

ADVICE = "Use the envcloak MCP (env_read) for a masked, safe view."


def is_protected_basename(base: str) -> bool:
    base = base.strip().strip("'\"")
    if base == ".env":
        return True
    if base.startswith(".env."):
        return not SAFE_SUFFIX.search(base)
    return False


def is_protected_path(p) -> bool:
    if not p:
        return False
    return is_protected_basename(os.path.basename(str(p).strip().strip("'\"")))


def deny_tool(reason: str) -> None:
    print(json.dumps({"hookSpecificOutput": {
        "hookEventName": "PreToolUse",
        "permissionDecision": "deny",
        "permissionDecisionReason": reason,
    }}))
    sys.exit(0)


def block_prompt(reason: str) -> None:
    print(json.dumps({"decision": "block", "reason": reason}))
    sys.exit(0)


def handle_pretooluse(data: dict) -> None:
    tool = data.get("tool_name", "")
    ti = data.get("tool_input", {}) or {}

    if tool == "Bash":
        cmd = ti.get("command", "") or ""
        for m in BASH_ENV_TOKEN.finditer(cmd):
            if is_protected_basename(os.path.basename(m.group(1))):
                deny_tool(f"Bash command references protected env file "
                          f"'{m.group(1)}'. {ADVICE}")
        return

    for key in ("file_path", "path", "notebook_path"):
        if is_protected_path(ti.get(key)):
            deny_tool(f"Access to env file '{ti.get(key)}' is blocked by policy. {ADVICE}")

    # Grep/Glob explicitly aimed at .env files
    if tool in ("Grep", "Glob"):
        for key in ("glob", "pattern"):
            v = ti.get(key)
            if v and re.search(r"\.env(\.|\*|$)", str(v)):
                deny_tool(f"{tool} targeting .env files is blocked by policy. {ADVICE}")


def handle_prompt(data: dict) -> None:
    prompt = data.get("prompt", "") or ""
    for m in MENTION_ENV.finditer(prompt):
        if is_protected_basename(os.path.basename(m.group(1))):
            block_prompt(
                f"This prompt @-mentions a protected env file ('{m.group(1)}'), which "
                f"would inline its raw secrets. {ADVICE} Re-ask without the @-mention."
            )


def main() -> None:
    try:
        data = json.load(sys.stdin)
    except Exception:
        sys.exit(0)  # never break a session on bad/empty input
    event = data.get("hook_event_name") or data.get("hookEventName")
    if event == "PreToolUse":
        handle_pretooluse(data)
    elif event == "UserPromptSubmit":
        handle_prompt(data)
    sys.exit(0)


if __name__ == "__main__":
    main()
