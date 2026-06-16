#!/usr/bin/env python3
"""envcloak MCP server (stdio, zero dependencies).

Exposes env-file tools to an agent while never revealing real secret values:
reads return a length-preserving *blurred* view, and edits never require the
agent to know a secret. See README.md for the full rationale.

Protocol: JSON-RPC 2.0 over newline-delimited stdin/stdout (MCP stdio
transport). Logging goes to stderr only — stdout must carry protocol only.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import envcloak_core as eg  # noqa: E402

SERVER_NAME = "envcloak"
SERVER_VERSION = "1.0.0"
PROTOCOL_VERSION = "2024-11-05"

# Only files that look like env files may be touched (defensive default).
ENV_BASENAME_HINTS = (".env",)


def log(*args) -> None:
    print("[envcloak]", *args, file=sys.stderr, flush=True)


def load_config() -> dict:
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json")
    try:
        with open(path, encoding="utf-8") as f:
            return eg.merge_config(json.load(f))
    except FileNotFoundError:
        return eg.merge_config(None)
    except Exception as e:  # noqa: BLE001
        log("config.json ignored:", e)
        return eg.merge_config(None)


CONFIG = load_config()
EXTRA_PATH_GLOBS = CONFIG.get("allowed_path_globs", [])


def is_env_path(path: str) -> bool:
    base = os.path.basename(path)
    if base == ".env" or base.startswith(".env") or base.endswith(".env"):
        return True
    import fnmatch
    return any(fnmatch.fnmatch(path, g) or fnmatch.fnmatch(base, g) for g in EXTRA_PATH_GLOBS)


def _guard_path(path: str) -> None:
    if not is_env_path(path):
        raise ValueError(
            f"refusing non-env path {path!r}. Allowed: *.env, .env, .env.*  "
            "(extend via allowed_path_globs in config.json)"
        )


def read_file(path: str) -> str:
    with open(path, encoding="utf-8") as f:
        return f.read()


def write_file_atomic(path: str, content: str) -> None:
    """Write atomically, preserving the original file mode if it exists."""
    d = os.path.dirname(os.path.abspath(path)) or "."
    mode = None
    if os.path.exists(path):
        mode = os.stat(path).st_mode
    fd, tmp = tempfile.mkstemp(dir=d, prefix=".envcloak.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(content)
        if mode is not None:
            os.chmod(tmp, mode)
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def blurred_report(path: str, text: str) -> str:
    """Human/agent-facing report: blurred body + a key summary."""
    blurred = eg.render_blurred(text, CONFIG)
    rows = eg.summarize(text, CONFIG)
    masked = sum(1 for r in rows if not r["revealed"])
    lines = [
        f"# {path}  ({len(rows)} vars, {masked} masked, {len(rows) - masked} revealed)",
        "# masked values are FAKE (real length/shape kept, content hidden) — never write one back.",
        "",
        blurred.rstrip("\n"),
        "",
        "# --- summary ---",
    ]
    for r in rows:
        tag = "clear" if r["revealed"] else "MASKED"
        lines.append(
            f"#   L{r['line']:<4} {r['key']:<28} [{tag}] len={r['value_len']}"
            + (" quoted" if r["quoted"] else "")
        )
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# Tool implementations  -> return the text payload for the agent
# --------------------------------------------------------------------------- #


def tool_read(args: dict) -> str:
    path = args["path"]
    _guard_path(path)
    return blurred_report(path, read_file(path))


def _mutate(path: str, new_text: str, summary: str) -> str:
    write_file_atomic(path, new_text)
    return summary + "\n\n" + blurred_report(path, new_text)


def tool_set_value(args: dict) -> str:
    path = args["path"]
    _guard_path(path)
    text = read_file(path)
    new = eg.set_value(text, args["key"], args["value"], CONFIG)
    return _mutate(path, new, f"OK: set value of {args['key']!r}")


def tool_rename_key(args: dict) -> str:
    path = args["path"]
    _guard_path(path)
    text = read_file(path)
    new = eg.rename_key(text, args["old_key"], args["new_key"])
    return _mutate(path, new, f"OK: renamed {args['old_key']!r} -> {args['new_key']!r}")


def tool_add_key(args: dict) -> str:
    path = args["path"]
    _guard_path(path)
    text = read_file(path)
    new = eg.add_key(text, args["key"], args["value"])
    return _mutate(path, new, f"OK: added {args['key']!r}")


def tool_delete_key(args: dict) -> str:
    path = args["path"]
    _guard_path(path)
    text = read_file(path)
    new = eg.delete_key(text, args["key"])
    return _mutate(path, new, f"OK: deleted {args['key']!r}")


def tool_replace_range(args: dict) -> str:
    path = args["path"]
    _guard_path(path)
    text = read_file(path)
    new = eg.replace_range(text, int(args["start"]), int(args["end"]),
                           args["replacement"], CONFIG)
    return _mutate(path, new, f"OK: replaced [{args['start']}, {args['end']})")


def tool_create_file(args: dict) -> str:
    path = args["path"]
    _guard_path(path)
    if os.path.exists(path) and not args.get("confirm"):
        raise ValueError(f"{path!r} exists; pass confirm=true to overwrite")
    write_file_atomic(path, args.get("content", ""))
    return f"OK: created {path!r}\n\n" + blurred_report(path, read_file(path))


def tool_delete_file(args: dict) -> str:
    path = args["path"]
    _guard_path(path)
    if not args.get("confirm"):
        raise ValueError(f"refusing to delete {path!r}; pass confirm=true")
    os.unlink(path)
    return f"OK: deleted {path!r}"


_P = {"path": {"type": "string", "description": "path to an env file (*.env, .env, .env.*)"}}

TOOLS = [
    {
        "name": "env_read",
        "description": "Read an env file and return a length-preserving BLURRED view "
                       "(secret values masked, config/booleans shown) plus a key summary. "
                       "Masked values are fake — never write them back.",
        "inputSchema": {"type": "object", "properties": dict(_P), "required": ["path"]},
        "_fn": tool_read,
    },
    {
        "name": "env_set_value",
        "description": "Set the real value of an existing key. Rejects an echo of the "
                       "masked value (round-trip guard).",
        "inputSchema": {"type": "object", "properties": {
            **_P, "key": {"type": "string"}, "value": {"type": "string"}},
            "required": ["path", "key", "value"]},
        "_fn": tool_set_value,
    },
    {
        "name": "env_rename_key",
        "description": "Rename a variable; its value is preserved untouched (you do not "
                       "need to know the value).",
        "inputSchema": {"type": "object", "properties": {
            **_P, "old_key": {"type": "string"}, "new_key": {"type": "string"}},
            "required": ["path", "old_key", "new_key"]},
        "_fn": tool_rename_key,
    },
    {
        "name": "env_add_key",
        "description": "Add a new key (errors if it already exists).",
        "inputSchema": {"type": "object", "properties": {
            **_P, "key": {"type": "string"}, "value": {"type": "string"}},
            "required": ["path", "key", "value"]},
        "_fn": tool_add_key,
    },
    {
        "name": "env_delete_key",
        "description": "Delete a key and its line(s).",
        "inputSchema": {"type": "object", "properties": {
            **_P, "key": {"type": "string"}}, "required": ["path", "key"]},
        "_fn": tool_delete_key,
    },
    {
        "name": "env_replace_range",
        "description": "Advanced: splice [start,end) using char offsets from the blurred "
                       "view. Structural edits only — refuses to touch masked values.",
        "inputSchema": {"type": "object", "properties": {
            **_P, "start": {"type": "integer"}, "end": {"type": "integer"},
            "replacement": {"type": "string"}},
            "required": ["path", "start", "end", "replacement"]},
        "_fn": tool_replace_range,
    },
    {
        "name": "env_create_file",
        "description": "Create a new env file (optional content). confirm=true to overwrite.",
        "inputSchema": {"type": "object", "properties": {
            **_P, "content": {"type": "string"}, "confirm": {"type": "boolean"}},
            "required": ["path"]},
        "_fn": tool_create_file,
    },
    {
        "name": "env_delete_file",
        "description": "Delete an env file. Requires confirm=true.",
        "inputSchema": {"type": "object", "properties": {
            **_P, "confirm": {"type": "boolean"}}, "required": ["path"]},
        "_fn": tool_delete_file,
    },
]
TOOL_BY_NAME = {t["name"]: t for t in TOOLS}


# --------------------------------------------------------------------------- #
# JSON-RPC plumbing
# --------------------------------------------------------------------------- #


def make_result(req_id, result):
    return {"jsonrpc": "2.0", "id": req_id, "result": result}


def make_error(req_id, code, message):
    return {"jsonrpc": "2.0", "id": req_id, "error": {"code": code, "message": message}}


def handle(msg: dict):
    method = msg.get("method")
    req_id = msg.get("id")
    is_notification = "id" not in msg

    if method == "initialize":
        return make_result(req_id, {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {"tools": {}},
            "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
        })
    if method == "notifications/initialized" or is_notification:
        return None
    if method == "ping":
        return make_result(req_id, {})
    if method == "tools/list":
        return make_result(req_id, {"tools": [
            {k: v for k, v in t.items() if not k.startswith("_")} for t in TOOLS
        ]})
    if method == "tools/call":
        params = msg.get("params") or {}
        name = params.get("name")
        args = params.get("arguments") or {}
        tool = TOOL_BY_NAME.get(name)
        if not tool:
            return make_error(req_id, -32602, f"unknown tool: {name}")
        try:
            text = tool["_fn"](args)
            return make_result(req_id, {"content": [{"type": "text", "text": text}]})
        except (ValueError, KeyError, FileNotFoundError, OSError) as e:
            # Surface as a tool error (not a protocol error) so the agent can react.
            return make_result(req_id, {
                "content": [{"type": "text", "text": f"ERROR: {e}"}],
                "isError": True,
            })
        except Exception as e:  # noqa: BLE001
            log("unexpected:", repr(e))
            return make_error(req_id, -32603, f"internal error: {e}")
    return make_error(req_id, -32601, f"method not found: {method}")


def main():
    log(f"started v{SERVER_VERSION} (pid {os.getpid()})")
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError as e:
            log("bad json:", e)
            continue
        response = handle(msg)
        if response is not None:
            sys.stdout.write(json.dumps(response) + "\n")
            sys.stdout.flush()


if __name__ == "__main__":
    main()
