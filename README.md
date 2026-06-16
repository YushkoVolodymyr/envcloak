# envcloak

A tiny **MCP server** that lets a Claude Code agent read and edit `.env` files
**without ever seeing real secret values** — while keeping the file informative
enough to actually debug with.

- **Zero dependencies.** Pure Python 3.8+ standard library. No pip, no npm.
- **Self-contained.** Everything lives in this one folder.
- **Installable as a Claude Code plugin.** One command registers the MCP server
  and the `.env`-blocking hook — see [Install](#install-as-a-claude-code-plugin-recommended).
- **Tested.** `python3 -m unittest` (54 tests).

## How it works

When the agent reads a file it gets a **blurred, length-preserving** view:

```
NODE_ENV=production
API_KEY=xx_xxxx_0xX00XxXxxXXxxxxX0xxx0xx
DATABASE_URL=xxxxxxxx://xxxxx:x0xx0x@xx:0000/xxx
PORT=8080
```

The masking rules:

| Input            | Output     | Why                                            |
|------------------|------------|------------------------------------------------|
| Latin letter     | `x` / `X`  | hides content, keeps case & length             |
| digit            | `0`        | hides content, keeps length                    |
| punctuation/space| unchanged  | keeps structure (`://`, `@`, `.`, `-`, quotes) |
| non-ASCII        | `x`        | so unicode can't leak                          |

So a secret keeps its **shape** (a JWT still looks like `xxx.xxx.xxx`, a URL
keeps its scheme/host/port skeleton) but not its value. Crucially the blurred
text is the **exact same length** as the real file, character for character —
that is what makes char-range edits safe.

**Non-secret config is shown in clear** via an allow-list, because that is what
you need when debugging: `NODE_ENV`, `*_ENV`, `LOG_LEVEL`, `DEBUG`, `PORT`,
`*_PORT`, `TZ`, `LANG`, plus any boolean value (`true/false/yes/no/on/off`) and
empty values. Edit the list in [`config.json`](./config.json).

### Safety properties

- **No round-trip corruption.** The agent never needs to know a secret to edit
  one. `env_set_value` refuses to write a value that *looks like a mask* (only
  `x`/`X`/`0` + punctuation), so a masked value can't be echoed back over a real
  one. Renames touch only the key.
- **Structural-only range edits.** `env_replace_range` refuses to overlap any
  masked value (use `env_set_value`/`env_rename_key` for those).
- **Path fence.** Only env-looking files (`*.env`, `.env`, `.env.*`) are
  accepted. Extend via `allowed_path_globs` in `config.json`.
- **Atomic writes** preserving the original file mode.
- **File-level destructive ops require `confirm: true`** (`env_delete_file`,
  overwriting via `env_create_file`).

To make this airtight, the agent must not be able to read env files any other
way (Read tool, `cat`, grep, …). The **plugin does this for
you** — see [Install](#install-as-a-claude-code-plugin-recommended):

1. **A `PreToolUse` hook** ([`block-env-files.py`](./block-env-files.py)) — the
   enforcer. It blocks raw `.env` access (`Read`/`Edit`/`Write`/`Grep`/`Glob`/`Bash`,
   including `cat`/`grep`/wildcards/exfiltration) across every project in **every
   permission mode**.
2. **An allow rule** for the envcloak MCP tools baked into `~/.claude/settings.json`
   at SessionStart, so the secure `env_read` path never prompts.

> **Why no `permissions.deny` rules?** Earlier versions (≤1.0.2) also baked
> `Read(.env.*)` deny rules. That **breaks Claude Code's `auto` mode**: its safety
> classifier sees `env_read` reading a `.env` while a deny rule exists and rejects
> it as *"circumventing the deny rule by switching tools."* An allow rule can't
> override the classifier. So as of 1.1.0 the plugin injects **no** `.env` deny
> rules and, at SessionStart, **retracts** any it wrote before. Raw-read blocking
> is the hook's job; the deny rules were redundant with it anyway.

## Tools

| Tool                | Purpose                                                    |
|---------------------|------------------------------------------------------------|
| `env_read`          | Blurred view + per-key summary (masked vs clear)           |
| `env_set_value`     | Set an existing key's value (round-trip guarded)           |
| `env_rename_key`    | Rename a var; value preserved untouched                    |
| `env_add_key`       | Add a new key                                              |
| `env_delete_key`    | Delete a key                                               |
| `env_replace_range` | Advanced structural splice by blurred-view offsets         |
| `env_create_file`   | Create a new env file (`confirm` to overwrite)             |
| `env_delete_file`   | Delete a file (`confirm` required)                         |

## Install as a Claude Code plugin (recommended)

envcloak ships as a self-contained plugin: installing it registers the
`envcloak` MCP server **and** wires the `.env`-blocking hook for you — no
manual `settings.json` editing. The repo is its own marketplace, so:

```text
# In Claude Code:
/plugin marketplace add YushkoVolodymyr/envcloak
/plugin install envcloak@envcloak
```

Then restart Claude Code (or run `/hooks`). Verify with:

```bash
claude mcp list        # -> envcloak: ... ✔ Connected
```

Requirement: a Python 3.8+ interpreter on PATH. The MCP server and hooks launch
as `${ENVCLOAK_PYTHON:-python3}`, and a `SessionStart` hook **auto-detects** the
right interpreter so you usually don't have to: it probes `python3`, `python`,
`python3.x` and the Windows `py -3` launcher, **runs** each candidate to confirm
it actually works (this skips the Microsoft Store `python3` alias, which
resolves on PATH but exits without running), then bakes the winner's absolute
path into `~/.claude/settings.json` → `env.ENVCLOAK_PYTHON`. **Restart Claude
Code once** after first install so the server and hook pick it up.

The detector is `tools/resolve-python.sh` (one POSIX-shell hook entry covering
Linux, macOS, and Windows under Git Bash / MSYS2). On **native Windows with no
POSIX shell**, run the PowerShell equivalent once, then restart:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File tools\resolve-python.ps1
```

You can always override detection by setting `ENVCLOAK_PYTHON` yourself (it
takes priority):

```bash
export ENVCLOAK_PYTHON=python     # macOS/Linux with only `python`
setx ENVCLOAK_PYTHON py           # Windows, py launcher (then restart)
```

Zero Python dependencies.

What the plugin activates (in every project, while enabled):

- a **`SessionStart` hook** that (a) auto-detects a working Python interpreter
  and bakes it into `~/.claude/settings.json` (see the requirement note above),
  (b) merges `.env` `permissions.deny` rules + an `mcp__envcloak` `allow` rule
  into the same file, and (c) injects a one-line reminder into the session so the
  agent reaches for `env_read` first;
- the **`envcloak` MCP server** (the `env_read` / `env_set_value` / … tools);
- a **`PreToolUse` hook** that blocks raw `.env` access via
  `Read`/`Edit`/`Write`/`Grep`/`Glob`/`Bash`. The `Bash` gate covers bash,
  `cmd.exe` and PowerShell — it blocks reads (including wildcards like `.e*`
  that the shell would expand to a real `.env`) and blocks copy/move/rename
  commands that would land a protected file under a readable name (secret
  exfiltration). Renaming/moving **between** protected names is allowed
  (e.g. `mv .env.local .env`).

Example/template files (`.env.example`, `.env.sample`, `.env.template`,
`.env.dist`, `.env.schema`) carry no secrets and stay readable.

Disable or remove anytime with `/plugin` (or `/plugin uninstall envcloak@envcloak`).

## Permission rules (automatic)

The `SessionStart` hook reconciles `~/.claude/settings.json` for you — no manual
editing, idempotent, and it preserves your unrelated rules:

```json
{
  "permissions": {
    "allow": [
      "mcp__plugin_envcloak_envcloak", "mcp__plugin_envcloak_envcloak__env_read", "…",
      "mcp__envcloak", "mcp__envcloak__env_read", "…"
    ]
  }
}
```

The `allow` list covers the secure tools under **both** name shapes Claude Code
might use: the plugin-namespaced `mcp__plugin_envcloak_envcloak__<tool>` (what a
plugin install actually invokes) and the plain `mcp__envcloak__<tool>` (a
non-plugin `claude mcp add` install) — server-wide *and* per-tool for each. The
per-tool, correctly-namespaced entries are what `auto` mode matches; that mode
auto-denies any tool not matched by name in `permissions.allow`, so a wrong or
missing name means the secure `env_read` is rejected there.

**No `.env` deny rules.** The hook does not add `permissions.deny` entries, and at
SessionStart it **retracts** any envcloak wrote in ≤1.0.2 (`Read/Edit/Write` on
`.env`, `.env.*`, the old `**/` variants, and config-derived globs). They broke
`auto` mode (the classifier flagged `env_read` as deny-rule circumvention) and
were redundant with the always-on `PreToolUse` hook.

If you run in a strict, non-`auto` setup and *want* belt-and-suspenders settings
denies, add them by hand — but expect `auto` mode to then reject `env_read`.

## Configure

[`config.json`](./config.json):

```json
{
  "reveal_keys": ["NODE_ENV", "*_ENV", "LOG_LEVEL", "DEBUG", "PORT", "*_PORT"],
  "reveal_boolean_values": true,
  "reveal_numeric_values": false,
  "partial_reveal": 0,
  "allowed_path_globs": []
}
```

- `reveal_keys` — glob patterns (case-insensitive) shown in clear.
- `reveal_numeric_values` — reveal plain numbers (off by default: a long digit
  run can be a secret).
- `partial_reveal` — keep N real chars at each end of a masked value
  (dashboard-style "ends in a3f2"). `0` = fully masked. Leaks a few bytes when set.
- `allowed_path_globs` — extra path/basename globs beyond `*.env` that the MCP
  will touch (a custom env-file location). Note: the `PreToolUse` hook protects
  `.env`/`.env.*` by basename; extend the hook separately if your custom files
  don't match that. Changes apply on the next `/plugin` reload + restart.

## Develop / test

```bash
python3 -m unittest -v          # run the 54 tests
```

`envcloak_core.py` is pure logic (parse / blur / edit) and has no I/O, so it is
trivially testable. `server.py` is the thin MCP/JSON-RPC stdio wrapper.

## Known limitations

- Length is preserved, which leaks the *length* of a secret (fine for almost all
  threat models; turn values into fixed-width if that matters to you).
- The dotenv parser handles comments, `export `, single/double quotes, inline
  comments, and multiline quoted values — but not every exotic shell-expansion
  edge case. `${VAR}` interpolation is masked as ordinary text.
