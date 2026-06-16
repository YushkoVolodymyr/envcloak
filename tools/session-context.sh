#!/bin/sh
# envcloak: inject concise standing guidance into each session's context.
# SessionStart hook stdout is added to the model's context, so this tells the
# agent to use the secure MCP path for .env files (belt-and-suspenders with the
# permission deny rules + the PreToolUse hook). Kept short on purpose. Exits 0.
printf '%s\n' 'envcloak active: .env files are protected. Read or edit ANY .env file ONLY through the envcloak MCP tools (env_read, env_set_value, env_add_key, env_rename_key, env_delete_key, env_create_file, env_delete_file) — never Read/Edit/Write/cat/grep them. env_read returns FAKE values (content masked, real length and structure preserved); use them to see shape only, and never write a masked value back.'
exit 0
