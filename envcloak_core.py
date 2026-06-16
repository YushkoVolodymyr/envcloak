"""envcloak core: a safe, length-preserving dotenv reader/editor.

This module is pure logic (no I/O, no MCP) so it can be unit-tested in isolation.

Design goals
------------
* **Blur secrets, keep structure.** Real values are masked character-class wise
  (Latin letter -> x/X, digit -> 0) so the *shape* survives: a JWT still looks
  like three dot-separated segments, a UUID still looks like a UUID, a URL keeps
  its ``://`` and ``@``. Punctuation/whitespace are preserved; non-ASCII chars
  are blurred too (so unicode can't leak).
* **Length preserving 1:1.** The blurred render has the exact same character
  count as the real file. That is what makes char-range edits safe: offsets the
  agent computes against the blurred view map onto the real file.
* **Stay informative.** Non-secret config (NODE_ENV, LOG_LEVEL, PORT, booleans,
  ...) is *revealed in clear* via an allow-list, because that is the stuff you
  actually need when debugging.
* **No round-trip corruption.** Edits never require the agent to know a secret.
  Renames touch only the key; value edits reject an echo of the blurred value.
"""

from __future__ import annotations

import fnmatch
import re
from typing import Any

# --------------------------------------------------------------------------- #
# Configuration
# --------------------------------------------------------------------------- #

DEFAULT_CONFIG: dict[str, Any] = {
    # Keys whose values are shown in clear (glob patterns, case-insensitive).
    # These are configuration, not credentials.
    "reveal_keys": [
        "NODE_ENV", "ENV", "ENVIRONMENT", "APP_ENV", "*_ENV",
        "LOG_LEVEL", "LOGLEVEL", "DEBUG", "VERBOSE", "TRACE",
        "PORT", "*_PORT",
        "TZ", "LANG", "LC_*",
    ],
    # Reveal values that are plainly non-secret regardless of key name.
    "reveal_boolean_values": True,   # true/false/yes/no/on/off
    "reveal_numeric_values": False,  # 123 / 1.5  (off: a long digit run can be a secret)
    # Keep N real characters at each end of a blurred secret (dashboard-style
    # "ends in a3f2"). 0 = fully blur. Leaks a few bytes when > 0.
    "partial_reveal": 0,
    # Blur character map.
    "blur_upper": "X",
    "blur_lower": "x",
    "blur_digit": "0",
    "blur_other": "x",  # non-ASCII / anything not punctuation
}

BOOL_VALUES = {"true", "false", "yes", "no", "on", "off"}
KEY_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_.\-]*")
NUMERIC_RE = re.compile(r"[+-]?\d+(\.\d+)?")
EXPORT_RE = re.compile(r"export[ \t]+")


def merge_config(overrides: dict | None) -> dict:
    cfg = dict(DEFAULT_CONFIG)
    if overrides:
        cfg.update({k: v for k, v in overrides.items() if v is not None})
    return cfg


# --------------------------------------------------------------------------- #
# Parser
# --------------------------------------------------------------------------- #
#
# We parse the *whole text* with absolute character offsets so every edit is a
# surgical string splice that preserves the rest of the file byte-for-byte.
# A "record" is one logical line. Entry records expose the spans we care about:
#   value_content_(start|end) -> the value characters to blur (inside quotes)
#   value_raw_(start|end)     -> the value token incl. quotes (what set_value replaces)


def _parse_value(text: str, j: int, line_end: int, full_end: int, n: int) -> dict:
    """Parse a value starting at offset ``j`` (first non-space after '=')."""
    if j >= line_end:  # empty value: KEY=
        return {
            "content_start": j, "content_end": j,
            "raw_start": j, "raw_end": j,
            "quote": None, "logical_end": full_end,
        }
    c = text[j]
    if c in ("'", '"'):
        quote = c
        content_start = j + 1
        k = content_start
        while k < n:
            ch = text[k]
            if quote == '"' and ch == "\\":  # backslash escape only in double quotes
                k += 2
                continue
            if ch == quote:
                break
            k += 1
        if k < n and text[k] == quote:
            content_end = k
            raw_end = k + 1
        else:  # unterminated quote -> run to end of text
            content_end = min(k, n)
            raw_end = content_end
        nl = text.find("\n", raw_end)
        logical_end = n if nl == -1 else nl + 1
        return {
            "content_start": content_start, "content_end": content_end,
            "raw_start": j, "raw_end": raw_end,
            "quote": quote, "logical_end": logical_end,
        }
    # unquoted: value runs to an inline " #" comment or end of line
    k = j
    comment_start = None
    while k < line_end:
        if text[k] == "#" and k > j and text[k - 1] in " \t":
            comment_start = k
            break
        k += 1
    raw_end = comment_start if comment_start is not None else line_end
    ve = raw_end
    while ve > j and text[ve - 1] in " \t":  # strip trailing spaces
        ve -= 1
    return {
        "content_start": j, "content_end": ve,
        "raw_start": j, "raw_end": ve,
        "quote": None, "logical_end": full_end,
    }


def parse(text: str) -> list[dict]:
    records: list[dict] = []
    n = len(text)
    pos = 0
    while pos < n:
        line_start = pos
        nl = text.find("\n", pos)
        line_end = n if nl == -1 else nl
        full_end = n if nl == -1 else nl + 1
        segment = text[line_start:line_end]
        stripped = segment.lstrip()
        indent = len(segment) - len(stripped)

        if stripped == "":
            records.append({"type": "blank", "start": line_start, "end": full_end})
            pos = full_end
            continue
        if stripped.startswith("#"):
            records.append({"type": "comment", "start": line_start, "end": full_end})
            pos = full_end
            continue

        cur = line_start + indent
        export = False
        m_exp = EXPORT_RE.match(text, cur)
        if m_exp and m_exp.end() <= line_end:
            export = True
            cur = m_exp.end()

        m_key = KEY_RE.match(text, cur)
        if not m_key or m_key.end() > line_end:
            records.append({"type": "other", "start": line_start, "end": full_end})
            pos = full_end
            continue
        key_start, key_end = m_key.start(), m_key.end()

        j = key_end
        while j < line_end and text[j] in " \t":
            j += 1
        if j >= line_end or text[j] != "=":
            records.append({"type": "other", "start": line_start, "end": full_end})
            pos = full_end
            continue
        eq = j
        j += 1
        while j < line_end and text[j] in " \t":
            j += 1

        val = _parse_value(text, j, line_end, full_end, n)
        records.append({
            "type": "entry",
            "start": line_start,
            "end": val["logical_end"],
            "export": export,
            "key": m_key.group(0),
            "key_start": key_start,
            "key_end": key_end,
            "eq": eq,
            "value_content_start": val["content_start"],
            "value_content_end": val["content_end"],
            "value_raw_start": val["raw_start"],
            "value_raw_end": val["raw_end"],
            "quote": val["quote"],
        })
        pos = val["logical_end"]
    return records


# --------------------------------------------------------------------------- #
# Blur engine
# --------------------------------------------------------------------------- #


def _blur_char(c: str, cfg: dict) -> str:
    if c in "\n\r":
        return c
    o = ord(c)
    if o < 128:
        if 65 <= o <= 90:
            return cfg["blur_upper"]
        if 97 <= o <= 122:
            return cfg["blur_lower"]
        if 48 <= o <= 57:
            return cfg["blur_digit"]
        return c  # ASCII punctuation / space / tab -> structural, keep
    return cfg["blur_other"]  # non-ASCII -> blur so unicode can't leak


def blur_value(s: str, cfg: dict) -> str:
    p = int(cfg.get("partial_reveal", 0) or 0)
    if p > 0 and len(s) > 2 * p:
        middle = "".join(_blur_char(c, cfg) for c in s[p:len(s) - p])
        return s[:p] + middle + s[len(s) - p:]
    return "".join(_blur_char(c, cfg) for c in s)


def key_is_revealed(key: str, cfg: dict) -> bool:
    ku = key.upper()
    return any(fnmatch.fnmatchcase(ku, pat.upper()) for pat in cfg.get("reveal_keys", []))


def value_is_revealed(value: str, cfg: dict) -> bool:
    v = value.strip()
    if not v:
        return True
    if cfg.get("reveal_boolean_values") and v.lower() in BOOL_VALUES:
        return True
    if cfg.get("reveal_numeric_values") and NUMERIC_RE.fullmatch(v):
        return True
    return False


def entry_is_revealed(rec: dict, text: str, cfg: dict) -> bool:
    if key_is_revealed(rec["key"], cfg):
        return True
    value = text[rec["value_content_start"]:rec["value_content_end"]]
    return value_is_revealed(value, cfg)


def render_blurred(text: str, cfg: dict | None = None) -> str:
    """Return a same-length copy of ``text`` with secret values blurred."""
    cfg = merge_config(cfg)
    out = list(text)
    for rec in parse(text):
        if rec["type"] != "entry":
            continue
        cs, ce = rec["value_content_start"], rec["value_content_end"]
        if cs == ce or entry_is_revealed(rec, text, cfg):
            continue
        blurred = blur_value(text[cs:ce], cfg)
        out[cs:ce] = list(blurred)
    return "".join(out)


def summarize(text: str, cfg: dict | None = None) -> list[dict]:
    cfg = merge_config(cfg)
    rows: list[dict] = []
    for rec in parse(text):
        if rec["type"] != "entry":
            continue
        revealed = entry_is_revealed(rec, text, cfg)
        rows.append({
            "key": rec["key"],
            "line": text.count("\n", 0, rec["start"]) + 1,
            "revealed": revealed,
            "value_len": rec["value_content_end"] - rec["value_content_start"],
            "quoted": rec["quote"] is not None,
            "exported": rec["export"],
        })
    return rows


# --------------------------------------------------------------------------- #
# Edit operations  (each returns new text; raises ValueError on misuse)
# --------------------------------------------------------------------------- #


def _find_entry(text: str, key: str) -> dict | None:
    for rec in parse(text):
        if rec["type"] == "entry" and rec["key"] == key:
            return rec
    return None


def _serialize_value(value: str) -> str:
    """Render a real value as a dotenv token, quoting only when needed."""
    if value == "":
        return ""
    needs_quote = (
        value != value.strip()
        or any(ch in value for ch in " \t#'\"")
        or "\n" in value
    )
    if not needs_quote:
        return value
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def looks_masked(value: str, cfg: dict) -> bool:
    """True if every alphanumeric char in ``value`` is already a blur char.

    Real secrets are essentially never composed solely of x/X/0 + punctuation,
    so this catches a masked value being written back even if it does not match
    the original byte-for-byte (e.g. an agent retyping the mask)."""
    if not any(c.isalnum() for c in value):
        return False  # pure punctuation/empty: can't be a masked secret echo
    return blur_value(value, cfg) == value


def set_value(text: str, key: str, new_value: str, cfg: dict | None = None) -> str:
    """Replace an existing key's value. Rejects an echo of the blurred value."""
    cfg = merge_config(cfg)
    rec = _find_entry(text, key)
    if rec is None:
        raise ValueError(f"key {key!r} not found; use add_key to create it")
    current = text[rec["value_content_start"]:rec["value_content_end"]]
    if not entry_is_revealed(rec, text, cfg) and (
        new_value == blur_value(current, cfg) or looks_masked(new_value, cfg)
    ):
        raise ValueError(
            f"refusing to write a masked value to {key!r} (round-trip guard): the value "
            "looks like a mask (only x/X/0 + punctuation). Provide the real value."
        )
    serialized = _serialize_value(new_value)
    return text[:rec["value_raw_start"]] + serialized + text[rec["value_raw_end"]:]


def rename_key(text: str, old_key: str, new_key: str) -> str:
    """Rename a variable, leaving its (unknown) value untouched."""
    if not KEY_RE.fullmatch(new_key):
        raise ValueError(f"invalid env var name: {new_key!r}")
    rec = _find_entry(text, old_key)
    if rec is None:
        raise ValueError(f"key {old_key!r} not found")
    if _find_entry(text, new_key) is not None:
        raise ValueError(f"key {new_key!r} already exists")
    return text[:rec["key_start"]] + new_key + text[rec["key_end"]:]


def add_key(text: str, key: str, value: str) -> str:
    if not KEY_RE.fullmatch(key):
        raise ValueError(f"invalid env var name: {key!r}")
    if _find_entry(text, key) is not None:
        raise ValueError(f"key {key!r} already exists; use set_value to change it")
    line = f"{key}={_serialize_value(value)}\n"
    if text and not text.endswith("\n"):
        return text + "\n" + line
    return text + line


def delete_key(text: str, key: str) -> str:
    rec = _find_entry(text, key)
    if rec is None:
        raise ValueError(f"key {key!r} not found")
    return text[:rec["start"]] + text[rec["end"]:]


def replace_range(text: str, start: int, end: int, replacement: str,
                  cfg: dict | None = None) -> str:
    """Generic splice using offsets from the blurred view.

    Restricted to *structural* edits: it refuses to touch any blurred value span
    (use set_value / rename_key for those) and refuses an echo of blurred text.
    """
    cfg = merge_config(cfg)
    n = len(text)
    if not (0 <= start <= end <= n):
        raise ValueError(f"range out of bounds: [{start}, {end}) for length {n}")
    blurred = render_blurred(text, cfg)
    if replacement == blurred[start:end]:
        raise ValueError("replacement equals the blurred slice (no-op / echo); rejected")
    for rec in parse(text):
        if rec["type"] != "entry" or entry_is_revealed(rec, text, cfg):
            continue
        cs, ce = rec["value_content_start"], rec["value_content_end"]
        if start < ce and cs < end:  # intersects a blurred value
            raise ValueError(
                f"range intersects the masked value of {rec['key']!r}; "
                "use set_value or rename_key instead of replace_range"
            )
    return text[:start] + replacement + text[end:]
