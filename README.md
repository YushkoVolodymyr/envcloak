# envcloak

A tiny **MCP server** that lets a Claude Code agent read and edit `.env` files
**without ever seeing real secret values** — while keeping the file informative
enough to actually debug with.

- **Zero dependencies.** Pure Python 3.8+ standard library. No pip, no npm.
- **Self-contained.** Everything lives in this one folder.
- **Installable as a Claude Code plugin.** One command registers the MCP server
  and the `.env`-blocking hook — see [Install](#install-as-a-claude-code-plugin-recommended).
- **Tested.** `python3 -m unittest` (33 tests).

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
way (Read tool, `cat`, grep, an `@`-mention, …). The **plugin does this for
you** — see [Install](#install-as-a-claude-code-plugin-recommended). It ships a
`PreToolUse` hook ([`block-env-files.py`](./block-env-files.py)) that blocks
`.env` access across every project, no matter which directory Claude is launched
from. (A hook is what makes this global and reliable — settings `permissions.deny`
rules only load for the exact project root you launch in.)

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

Requirement: `python3` on PATH (the MCP server and hook are launched as
`python3`). Zero Python dependencies.

What the plugin activates (in every project, while enabled):

- the **`envcloak` MCP server** (the `env_read` / `env_set_value` / … tools);
- a **`PreToolUse` hook** that blocks raw `.env` access via
  `Read`/`Edit`/`Write`/`Grep`/`Glob`/`Bash`;
- a **`UserPromptSubmit` hook** that blocks `@`-mentions of `.env` files
  (an `@`-mention would otherwise inline raw secrets, bypassing the tool gate).

Example/template files (`.env.example`, `.env.sample`, `.env.template`,
`.env.dist`, `.env.schema`) carry no secrets and stay readable.

Disable or remove anytime with `/plugin` (or `/plugin uninstall envcloak@envcloak`).

## Optional: extra hardening with `permissions.deny`

The hook is the real enforcement — it blocks every tool *and* `@`-mentions,
which settings denies alone can't. A plugin can't ship `permissions.deny`
entries, so if you also want belt-and-suspenders settings-level denies, add
these to your `~/.claude/settings.json` by hand:

```json
{
  "permissions": {
    "deny": [
      "Read(.env)", "Read(.env.*)", "Read(**/.env)", "Read(**/.env.*)",
      "Edit(.env)", "Edit(.env.*)", "Edit(**/.env)", "Edit(**/.env.*)",
      "Write(.env)", "Write(.env.*)", "Write(**/.env)", "Write(**/.env.*)",
      "Grep(**/.env*)"
    ]
  }
}
```

These only load for the project root you launch Claude in — which is exactly why
envcloak relies on the global hook instead. They're purely additive.

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
- `allowed_path_globs` — extra path/basename globs beyond `*.env`.

## Develop / test

```bash
python3 -m unittest -v          # run the 33 tests
```

`envcloak_core.py` is pure logic (parse / blur / edit) and has no I/O, so it is
trivially testable. `server.py` is the thin MCP/JSON-RPC stdio wrapper.

## Known limitations

- Length is preserved, which leaks the *length* of a secret (fine for almost all
  threat models; turn values into fixed-width if that matters to you).
- The dotenv parser handles comments, `export `, single/double quotes, inline
  comments, and multiline quoted values — but not every exotic shell-expansion
  edge case. `${VAR}` interpolation is masked as ordinary text.
