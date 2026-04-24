# Wolai MCP Local Notes

This folder contains repo-level notes and helper scripts for using the
`wolai-mcp` server from Codex.

Machine-local details belong in that machine's Codex config, not in tracked
files. Do not commit real credentials or personal absolute paths.

## Configuration Contract

- MCP server name: `wolai-kb`
- Helper default config path: `CODEX_CONFIG` when set, otherwise
  `~/.codex/config.toml`
- Wolai package: `wolai-mcp==1.1.0`
- Required env values in the Codex server config:
  `WOLAI_APP_ID`, `WOLAI_APP_SECRET`, `WOLAI_ROOT_ID`

The `command` in Codex config should point to the local `wolai-mcp` executable
for the current machine. Prefer an absolute path in the local config, but keep
tracked examples as placeholders.

```toml
[mcp_servers.wolai-kb]
command = "/absolute/path/to/Wolai_MCP/.venv/bin/wolai-mcp"

[mcp_servers.wolai-kb.env]
WOLAI_APP_ID = "..."
WOLAI_APP_SECRET = "..."
WOLAI_ROOT_ID = "fND6EnXuZdoA1RPavzgaSY"
```

Windows venvs usually place the executable under:

```toml
command = "C:\\absolute\\path\\to\\Wolai_MCP\\.venv\\Scripts\\wolai-mcp.exe"
```

Never print or write `WOLAI_APP_SECRET` into logs, docs, commits, or chat unless
the user explicitly requests secret inspection.

## Setup

POSIX shells:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install wolai-mcp==1.1.0
```

Windows PowerShell:

```powershell
py -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install wolai-mcp==1.1.0
```

After adding the machine-local Codex config, smoke test with the helper:

```bash
.venv/bin/python scripts/wolai_mcp_client.py doctor
.venv/bin/python scripts/wolai_mcp_client.py tools
.venv/bin/python scripts/wolai_mcp_client.py root
.venv/bin/python scripts/wolai_mcp_client.py children
```

PowerShell equivalents:

```powershell
.\.venv\Scripts\python.exe .\scripts\wolai_mcp_client.py doctor
.\.venv\Scripts\python.exe .\scripts\wolai_mcp_client.py tools
.\.venv\Scripts\python.exe .\scripts\wolai_mcp_client.py root
.\.venv\Scripts\python.exe .\scripts\wolai_mcp_client.py children
```

Expected root shape:

```text
Current Root Directory: 'Starla / Sail (1/7, 14.28%)' (ID: fND6EnXuZdoA1RPavzgaSY)
```

Codex may need a restart or new session before the native MCP tool list picks
up a newly added server.

## Helper Usage

List tools:

```bash
.venv/bin/python scripts/wolai_mcp_client.py tools
```

Check local config without printing secrets:

```bash
.venv/bin/python scripts/wolai_mcp_client.py doctor
```

List root children:

```bash
.venv/bin/python scripts/wolai_mcp_client.py children
```

Read a page:

```bash
.venv/bin/python scripts/wolai_mcp_client.py page fND6EnXuZdoA1RPavzgaSY
```

Debug reference expansion for one page:

```bash
.venv/bin/python scripts/wolai_mcp_client.py page-expanded rT8wZuoL6GKvVLi1VEP1SS --max-depth 4
```

Read database rows:

```bash
.venv/bin/python scripts/wolai_mcp_client.py database mjrgyUWX1NnNYmcHq2vMJ4
```

Print raw database JSON:

```bash
.venv/bin/python scripts/wolai_mcp_client.py database mjrgyUWX1NnNYmcHq2vMJ4 --raw
```

Search page titles:

```bash
.venv/bin/python scripts/wolai_mcp_client.py search Amazon --max-depth 2
```

## Reference Handling

Prefer targeted block-by-block resolution when accuracy matters:

- For a `[reference]` block, inspect raw OpenAPI JSON, read `source_block_id`,
  then read that source block as the visible content.
- For inline rich text `bi_link.block_id`, first read the target block and check
  its type. Expand only normal body elements such as text, headings, lists,
  callouts, quotes, equations, code, and references.
- Do not expand inline targets that are pages, databases, images, media, or other
  container/asset blocks; treat them as links or source pointers.
- Keep a per-read visited set and small max depth to avoid reference loops.

The `page-expanded` command is a debugging convenience for this process. It is
not meant to replace careful targeted inspection. Block JSON may be cached only
within one command run; each new invocation re-reads Wolai.

## Verification Notes

- The installed `wolai-mcp` command is a stdio MCP server.
- MCP initialization succeeds through Python's `mcp.client.stdio` client.
- Tool listing succeeds without contacting Wolai credentials.
- Live read tests require network access to `https://openapi.wolai.com`.
- In sandboxed Codex commands, DNS/network access may require escalation before
  `root`, `children`, `page`, `search`, or `database` can succeed.
- Wolai database rows can be read through `GET /v1/databases/{id}`. The helper
  script exposes this as the `database` command and prints row `page_id` values.
- Wolai reference blocks can hide answer content from MCP's simplified page
  rendering. Resolve block-level references via `source_block_id`, and resolve
  inline `bi_link.block_id` only when the target is a normal body block.
