#!/usr/bin/env python3
"""Bake the resolved Python interpreter into user-global Claude Code settings.

This is the cross-platform "brain" of envcloak's interpreter detection. It is
invoked by the per-OS shim (``resolve-python.sh`` on unix, ``resolve-python.ps1``
on Windows) *after* that shim has located a working Python 3 interpreter -- and
crucially, it runs **under that interpreter**, so ``sys.executable`` already is
the canonical absolute path we want to record.

Why this exists: envcloak's MCP server and PreToolUse hook resolve their
interpreter via ``${ENVCLOAK_PYTHON:-python3}``. On many machines the literal
name ``python3`` is missing or, on Windows, points at the Microsoft Store
execution-alias stub (which exits non-zero without running). When that happens
the plugin fails to start even though a perfectly good ``python`` / mise / pyenv
interpreter is on PATH. This script records the real interpreter into
``~/.claude/settings.json`` -> ``env.ENVCLOAK_PYTHON`` so the next Claude Code
launch spawns the MCP server and hook with a working interpreter.

Notes:
  * Pure stdlib, win32 + posix.
  * Idempotent: a no-op when the recorded value already matches.
  * Never clobbers an unreadable/invalid settings.json -- it leaves it untouched
    and just prints the resolved path so a human can wire it manually.
  * Writes are atomic (temp file + os.replace).
  * Takes effect on the NEXT Claude Code restart (env is applied at process
    start and inherited by the MCP server + hook subprocesses).
"""

from __future__ import annotations

import json
import os
import sys
import tempfile

MIN_VERSION = (3, 8)
ENV_KEY = "ENVCLOAK_PYTHON"

# IMPORTANT: envcloak does NOT add `.env` deny rules to settings.
#
# It used to (≤1.0.2). But Claude Code's `auto` permission mode runs an LLM
# "auto mode classifier" that auto-DENIES a tool when it looks like a way around
# another rule. With a `Read(.env.*)` deny rule present, calling the secure
# `env_read` MCP tool is flagged as "circumventing the deny rule by switching
# tools" and rejected -- so the deny rule we added to *steer* the agent actually
# *blocks the safe path*. An `allow` entry does not override the classifier.
#
# Enforcement of raw reads is the PreToolUse hook's job (it denies Read/Edit/
# Write/Bash on `.env` in every mode); the MCP is the sanctioned read path. So we
# (a) allow the MCP tools and (b) actively REMOVE any `.env` deny rules earlier
# versions baked in, to unbreak `auto` mode. These globs define what to remove.
BASE_PROTECTED_GLOBS = (".env", ".env.*")
# Enumerated suffixes the very first releases (1.0.0) wrote; kept so the cleanup
# retracts them too.
LEGACY_PROTECTED_GLOBS = (
    ".env.local", ".env.*.local", ".env.development", ".env.dev",
    ".env.production", ".env.prod", ".env.staging", ".env.test",
    ".env.secret", ".env.secrets",
)
DENY_TOOLS = ("Read", "Edit", "Write")

# Auto-approve the secure path so the fallback never prompts -- and, crucially,
# is not auto-DENIED in `dontAsk` ("auto") mode, which rejects any tool not
# matched BY NAME in `permissions.allow`.
#
# The tool name depends on how envcloak is installed. As a Claude Code *plugin*
# (the supported path), its MCP tools are namespaced `mcp__plugin_<plugin>_<server>__<tool>`
# -> `mcp__plugin_envcloak_envcloak__env_read`. A plain (non-plugin) `claude mcp add`
# install would expose them as `mcp__envcloak__env_read`. We allow BOTH the
# server-wide and every per-tool entry under each prefix, so `dontAsk` matches
# regardless of install shape. (A server-wide entry alone did NOT reliably match
# individual calls in dontAsk; the per-tool entries are the ones that do.)
ENV_TOOL_NAMES = (
    "env_read", "env_set_value", "env_add_key", "env_rename_key",
    "env_delete_key", "env_replace_range", "env_create_file", "env_delete_file",
)
_ALLOW_PREFIXES = ("mcp__plugin_envcloak_envcloak", "mcp__envcloak")
ALLOW_RULES = tuple(
    rule
    for prefix in _ALLOW_PREFIXES
    for rule in (prefix, *(f"{prefix}__{name}" for name in ENV_TOOL_NAMES))
)


def plugin_root() -> str:
    """Plugin install root (honours CLAUDE_PLUGIN_ROOT; else this file's parent)."""
    root = os.environ.get("CLAUDE_PLUGIN_ROOT")
    if root:
        return root
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def load_protected_globs() -> list:
    """Env-file globs to deny: the wildcard base plus config.json's
    ``allowed_path_globs``.

    Read fresh on every run, so editing config.json and re-running SessionStart
    (which a `/plugin` reload triggers) updates the deny rules. Additive only —
    removing a pattern from config does not retract an already-written rule.
    """
    globs = list(BASE_PROTECTED_GLOBS)
    try:
        with open(os.path.join(plugin_root(), "config.json"), encoding="utf-8") as f:
            extra = json.load(f).get("allowed_path_globs")
        if isinstance(extra, list):
            for g in extra:
                if isinstance(g, str) and g and g not in globs:
                    globs.append(g)
    except FileNotFoundError:
        pass
    except Exception as e:  # noqa: BLE001 - a bad config must never break the session
        log(f"config.json ignored for deny rules: {e}")
    return globs


def removable_deny_rules() -> set:
    """Every `.env` deny rule shape envcloak (or its old README) ever produced.

    Covers the base + legacy globs and the current config's ``allowed_path_globs``,
    each as `Tool(glob)` and `Tool(**/glob)` (the old README suggested the `**/`
    forms). Used to retract them from settings so `auto` mode stops flagging the
    secure MCP read as a deny-rule circumvention.
    """
    globs = set(load_protected_globs()) | set(LEGACY_PROTECTED_GLOBS)
    rules = set()
    for tool in DENY_TOOLS:
        for g in globs:
            rules.add(f"{tool}({g})")
            rules.add(f"{tool}(**/{g})")
    return rules


def log(*args: object) -> None:
    print("[envcloak resolve-python]", *args, file=sys.stderr, flush=True)


def settings_path() -> str:
    """User-global Claude Code settings.json (honours CLAUDE_CONFIG_DIR)."""
    base = os.environ.get("CLAUDE_CONFIG_DIR") or os.path.join(
        os.path.expanduser("~"), ".claude"
    )
    return os.path.join(base, "settings.json")


def load_settings(path: str):
    """Return (data, ok). data is None when the file exists but is unusable."""
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError:
        return {}, True
    except Exception as e:  # noqa: BLE001 - any parse/IO error: do not touch it
        log(f"refusing to edit unreadable {path} ({e}); leaving it untouched")
        return None, False
    if not isinstance(data, dict):
        log(f"refusing to edit {path}: top-level value is not an object")
        return None, False
    return data, True


def atomic_write(path: str, data: dict) -> None:
    directory = os.path.dirname(path) or "."
    os.makedirs(directory, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=directory, prefix=".settings.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
            f.write("\n")
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            try:
                os.unlink(tmp)
            except OSError:
                pass


def merge_permissions(data: dict) -> bool:
    """Reconcile ``permissions`` for envcloak. Returns True iff anything changed.

    1. RETRACT any `.env` deny rule envcloak (or its old README) introduced -- in
       `auto` mode these make the secure `env_read` call get auto-denied as a
       deny-rule circumvention. Raw-read enforcement stays with the PreToolUse hook.
    2. ENSURE the allow rules for the MCP tools are present (idempotent, additive;
       existing user rules and their order are preserved).
    """
    changed = False
    perms = data.get("permissions")
    if not isinstance(perms, dict):
        perms = {}

    removable = removable_deny_rules()
    deny = perms.get("deny")
    if isinstance(deny, list):
        kept = [r for r in deny if r not in removable]
        if len(kept) != len(deny):
            perms["deny"] = kept
            changed = True

    allow = perms.get("allow")
    if not isinstance(allow, list):
        allow = []
    seen = set(allow)
    for rule in ALLOW_RULES:
        if rule not in seen:
            allow.append(rule)
            seen.add(rule)
            changed = True
    perms["allow"] = allow

    if changed:
        data["permissions"] = perms
    return changed


def write_cache(resolved: str, version: str) -> None:
    """Best-effort diagnostic cache under the plugin's persistent data dir."""
    data_dir = os.environ.get("CLAUDE_PLUGIN_DATA")
    if not data_dir:
        return
    try:
        os.makedirs(data_dir, exist_ok=True)
        with open(os.path.join(data_dir, "interpreter.json"), "w", encoding="utf-8") as f:
            json.dump({"interpreter": resolved, "version": version}, f, indent=2)
            f.write("\n")
    except OSError as e:
        log(f"could not write interpreter cache: {e}")


def main() -> int:
    if sys.version_info[:2] < MIN_VERSION:
        log(
            f"this interpreter is {sys.version.split()[0]}, older than required "
            f"{MIN_VERSION[0]}.{MIN_VERSION[1]}; not baking"
        )
        return 0

    resolved = sys.executable or ""
    if not resolved or not os.path.isfile(resolved):
        log("sys.executable is empty or missing; cannot determine a real interpreter")
        return 0

    version = sys.version.split()[0]
    write_cache(resolved, version)

    path = settings_path()
    data, ok = load_settings(path)
    if not ok:
        # Unreadable settings: never clobber. Surface the path for manual wiring.
        print(resolved)
        return 0

    env = data.get("env")
    if not isinstance(env, dict):
        env = {}
    env_changed = env.get(ENV_KEY) != resolved
    if env_changed:
        env[ENV_KEY] = resolved
        data["env"] = env

    perms_changed = merge_permissions(data)

    if not (env_changed or perms_changed):
        print(resolved)  # already baked; nothing to do
        return 0

    atomic_write(path, data)
    if env_changed:
        log(f"baked {ENV_KEY}={resolved} into {path}")
    if perms_changed:
        log(f"reconciled envcloak permissions in {path} "
            "(allow MCP tools; retract any .env deny rules that break auto mode)")
    log("restart Claude Code so the MCP server, hook, and permission rules take effect")
    print(resolved)
    return 0


if __name__ == "__main__":
    sys.exit(main())
