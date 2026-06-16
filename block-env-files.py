#!/usr/bin/env python3
"""Block access to real .env files across every Claude Code session.

Registered as a user-global hook so it applies regardless of which directory
Claude is launched from (settings.local.json deny rules only load for the exact
project root, which is easy to miss in a multi-project workspace).

Handles one event:
  * PreToolUse      -> deny Read/Edit/Write/Grep/Glob/Bash that targets a
                       protected .env file (covers subagent tool calls too).

Protected = basename `.env` or `.env.<suffix>`, EXCEPT example/template files
(`.env.example`, `.env.sample`, `.env.template`, `.env.dist`, `.env.schema`),
which carry no secrets and stay readable.

Bash gate (bash, cmd.exe and PowerShell):
  * any command that *reads* a protected env file is denied (covers literal
    names and wildcards such as `.e*`/`.en?` that the shell would expand to a
    real `.env`);
  * copy/move/rename commands are denied when they would land a protected env
    file under a *non-protected* (readable) name -- i.e. secret exfiltration --
    but are ALLOWED when every path involved is itself a protected env file
    (e.g. `mv .env.local .env`).

Secrets should be read through the envcloak MCP (`env_read`), which returns a
masked, length-preserving view instead of raw values.
"""

import fnmatch
import json
import re
import sys

SAFE_SUFFIX = re.compile(r"\.(example|sample|template|dist|schema)$", re.IGNORECASE)
# a literal .env token as it appears inside a command line (covers / and \ paths)
BASH_ENV_TOKEN = re.compile(r"""(?:^|[\s=:"'(<>|&;/\\])(\.env(?:\.[A-Za-z0-9_.-]+)?)""")

ADVICE = "Use the envcloak MCP (env_read) for a masked, safe view."

# Commands that relocate a file under a (possibly) new name. We let these run
# when every path is a protected env file (a safe rename/move), but block them
# when a protected source would end up readable. Lowercased; covers bash,
# cmd.exe and PowerShell spellings + common aliases.
MOVE_RENAME_CMDS = {
    "cp", "mv",                                    # unix / pwsh aliases
    "copy", "move", "ren", "rename",               # cmd.exe builtins
    "xcopy", "robocopy",                           # cmd.exe tools
    "copy-item", "move-item", "rename-item",       # PowerShell cmdlets
    "cpi", "mi", "rni",                            # PowerShell aliases
}

_GLOB_CHARS = "*?["
# Representative names used to decide (filesystem-independently) whether a
# wildcard token could expand to a protected env file.
_PROTECTED_SAMPLES = (
    ".env", ".env.local", ".env.production", ".env.development",
    ".env.staging", ".env.test", ".env.secret", ".env.x",
)
_CONTROL_SAMPLES = (
    "main.py", "README.md", "index.js", "data.txt",
    "script.sh", "Makefile", "src", "package.json",
)

_SEP_RE = re.compile(r"\|\||&&|[;\n|&]")
_TOKEN_RE = re.compile(r"\"[^\"]*\"|'[^']*'|\S+")


def _unquote(token: str) -> str:
    token = token.strip()
    if len(token) >= 2 and token[0] == token[-1] and token[0] in "\"'":
        token = token[1:-1]
    return token


def _basename(token: str) -> str:
    token = _unquote(token).replace("\\", "/")
    return token.rsplit("/", 1)[-1]


def _has_glob(s: str) -> bool:
    return any(c in s for c in _GLOB_CHARS)


def is_protected_basename(base: str) -> bool:
    base = _unquote(base)
    if base == ".env":
        return True
    if base.startswith(".env."):
        return not SAFE_SUFFIX.search(base)
    return False


def is_protected_path(p) -> bool:
    if not p:
        return False
    return is_protected_basename(_basename(str(p)))


def glob_targets_env(base: str) -> bool:
    """True if a wildcard *basename* could expand to a protected env file.

    Catches bypasses like `.e*`, `.en?`, `.env*`, `[.]env` while leaving
    ordinary catch-alls (`*`, `*.py`) alone so we don't block `ls *`."""
    if not _has_glob(base):
        return False
    try:
        if not any(fnmatch.fnmatch(s, base) for s in _PROTECTED_SAMPLES):
            return False
        # a pattern that also matches unrelated files is a generic catch-all,
        # not an env-targeting glob -> leave it to ordinary handling.
        if any(fnmatch.fnmatch(c, base) for c in _CONTROL_SAMPLES):
            return False
    except re.error:
        return False
    return True


def _is_option(token: str) -> bool:
    t = _unquote(token)
    if t.startswith("-"):                       # unix / pwsh: -r, -Path, ...
        return True
    if re.match(r"^/[A-Za-z]$", t) or re.match(r"^/[A-Z]{2,6}$", t):
        return True                             # cmd.exe switches: /Y /S /MIR
    return False


def _literal_protected_refs(segment: str):
    out = []
    for m in BASH_ENV_TOKEN.finditer(segment):
        if is_protected_basename(_basename(m.group(1))):
            out.append(m.group(1))
    return out


def _analyze_segment(segment: str):
    tokens = _TOKEN_RE.findall(segment)
    if not tokens:
        return None

    literal_refs = _literal_protected_refs(segment)
    glob_refs = [t for t in tokens if glob_targets_env(_basename(t))]
    if not literal_refs and not glob_refs:
        return None

    cmdword = _basename(tokens[0]).lower()
    if cmdword in MOVE_RENAME_CMDS:
        file_tokens = [t for t in tokens[1:] if not _is_option(t)]
        bnames = [_basename(t) for t in file_tokens]
        # safe only when every path is a concrete (non-wildcard) protected env
        if bnames and all(is_protected_basename(b) and not _has_glob(b)
                          for b in bnames):
            return None
        ref = literal_refs[0] if literal_refs else _basename(glob_refs[0])
        return (f"Refusing to {cmdword} protected env file '{ref}' to a "
                f"non-protected name (possible secret exfiltration). "
                f"Copy/move/rename is only allowed when every path is itself a "
                f"protected env file (e.g. `mv .env.local .env`). {ADVICE}")

    ref = literal_refs[0] if literal_refs else _basename(glob_refs[0])
    return f"Bash command references protected env file '{ref}'. {ADVICE}"


def analyze_bash(cmd: str):
    """Return a deny reason if the command would read or exfiltrate a protected
    env file, else None. Pure and side-effect free so it can be unit-tested."""
    cmd = cmd or ""
    try:
        for segment in _SEP_RE.split(cmd):
            reason = _analyze_segment(segment)
            if reason:
                return reason
    except Exception:  # noqa: BLE001 - the gate must never crash; fail closed
        for m in BASH_ENV_TOKEN.finditer(cmd):
            if is_protected_basename(_basename(m.group(1))):
                return (f"Bash command references protected env file "
                        f"'{m.group(1)}'. {ADVICE}")
    return None


def deny_tool(reason: str) -> None:
    print(json.dumps({"hookSpecificOutput": {
        "hookEventName": "PreToolUse",
        "permissionDecision": "deny",
        "permissionDecisionReason": reason,
    }}))
    sys.exit(0)


def handle_pretooluse(data: dict) -> None:
    tool = data.get("tool_name", "")
    ti = data.get("tool_input", {}) or {}

    if tool == "Bash":
        reason = analyze_bash(ti.get("command", "") or "")
        if reason:
            deny_tool(reason)
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


def main() -> None:
    try:
        data = json.load(sys.stdin)
    except Exception:
        sys.exit(0)  # never break a session on bad/empty input
    event = data.get("hook_event_name") or data.get("hookEventName")
    if event == "PreToolUse":
        handle_pretooluse(data)
    sys.exit(0)


if __name__ == "__main__":
    main()
