# env-guard

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
way (Read tool, `cat`, grep, an `@`-mention, …). The **installer does this for
you** — see [Install](#install). It enforces, at user scope, a `PreToolUse`
hook ([`block-env-files.py`](./block-env-files.py)) plus `permissions.deny`
wildcards, so `.env` files are blocked across every project regardless of which
directory Claude is launched from. (Settings deny rules only load for the exact
project root you launch in — a hook is what makes it global and reliable.)

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

env-guard ships as a self-contained plugin: installing it registers the
`env-guard` MCP server **and** wires the `.env`-blocking hook for you — no
manual `settings.json` editing. The repo is its own marketplace, so:

```text
# In Claude Code:
/plugin marketplace add YushkoVolodymyr/env-guard
/plugin install env-guard@env-guard
```

Then restart Claude Code (or run `/hooks`). Verify with:

```bash
claude mcp list        # -> env-guard: ... ✔ Connected
```

Requirement: `python3` on PATH (the MCP server and hook are launched as
`python3`). Zero Python dependencies.

What the plugin activates (in every project, while enabled):

- the **`env-guard` MCP server** (the `env_read` / `env_set_value` / … tools);
- a **`PreToolUse` hook** that blocks raw `.env` access via
  `Read`/`Edit`/`Write`/`Grep`/`Glob`/`Bash`;
- a **`UserPromptSubmit` hook** that blocks `@`-mentions of `.env` files.
  (The manual installer below leaves this `@`-mention guard *off* by default;
  the plugin turns it *on* — a stronger default for a security tool.)

Example/template files (`.env.example`, `.env.sample`, `.env.template`,
`.env.dist`, `.env.schema`) carry no secrets and stay readable.

Disable or remove anytime with `/plugin` (or `/plugin uninstall env-guard@env-guard`).

> A plugin cannot ship `permissions.deny` entries, so the hook is the
> enforcement mechanism (it covers every tool + `@`-mentions, which deny rules
> alone do not). If you also want belt-and-suspenders `permissions.deny`
> wildcards, add them via the manual installer or your own `settings.json`.

## Install manually (without the plugin)

Requirements: `python3` and the `claude` CLI on PATH.

```bash
# 1. Put this folder anywhere, e.g.
git clone <repo> ~/.claude/mcp-servers/env-guard   # or copy the folder

# 2. Run the installer
~/.claude/mcp-servers/env-guard/install.sh

# 3. Restart Claude Code (or run /hooks), then verify:
claude mcp list        # -> env-guard: ... ✔ Connected
```

The installer is **idempotent** and path-agnostic (works wherever the folder
lives). It edits only user-global config (`~/.claude/`), backing up
`settings.json` to `settings.json.bak` first, and does four things:

1. Runs the test suite.
2. Registers the `env-guard` MCP server at **user scope** (all projects).
3. Installs the `PreToolUse` hook to `~/.claude/hooks/block-env-files.py`.
4. Merges into `~/.claude/settings.json` (adding only what's missing):
   - `permissions.allow += "mcp__env-guard"` — runs **only this** MCP server
     without a prompt; every other MCP keeps prompting as usual.
   - `permissions.deny += .env wildcards` — blocks `Read`/`Edit`/`Write`/`Grep`
     of `.env*`.
   - `hooks.PreToolUse` — wires the hook (the reliable, global enforcement).

By default the **`@`-mention guard is off** (it's commented at the end of the
installer output). Enable it by adding a `UserPromptSubmit` hook pointing at the
same script — see the installer's closing note.

Manual MCP registration only (no hook/settings), equivalent to step 2:

```bash
claude mcp add env-guard -s user -- python3 /ABSOLUTE/PATH/TO/env-guard/server.py
```

Scopes: `-s user` = all your projects (recommended). Use `-s project` to commit
a `.mcp.json` and share via the repo instead.

Uninstall: `claude mcp remove env-guard -s user`, delete
`~/.claude/hooks/block-env-files.py`, and remove the `env-guard` entries from
`~/.claude/settings.json` (`mcp__env-guard`, the `.env` denies, the PreToolUse
hook).

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

`envguard_core.py` is pure logic (parse / blur / edit) and has no I/O, so it is
trivially testable. `server.py` is the thin MCP/JSON-RPC stdio wrapper.

## Known limitations

- Length is preserved, which leaks the *length* of a secret (fine for almost all
  threat models; turn values into fixed-width if that matters to you).
- The dotenv parser handles comments, `export `, single/double quotes, inline
  comments, and multiline quoted values — but not every exotic shell-expansion
  edge case. `${VAR}` interpolation is masked as ordinary text.
