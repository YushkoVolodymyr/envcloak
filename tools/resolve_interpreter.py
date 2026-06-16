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

    if env.get(ENV_KEY) == resolved:
        print(resolved)  # already baked; nothing to do
        return 0

    env[ENV_KEY] = resolved
    data["env"] = env
    atomic_write(path, data)
    log(f"baked {ENV_KEY}={resolved} into {path}")
    log("restart Claude Code so the MCP server and hook pick up this interpreter")
    print(resolved)
    return 0


if __name__ == "__main__":
    sys.exit(main())
