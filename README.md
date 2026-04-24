# Wolai MCP Local Notes

This folder contains local operational notes and helper scripts for using the
`wolai-mcp` server from Codex on this Windows machine.

The important bit: do not depend on `wolai-mcp` being on `PATH`. Use the
absolute Anaconda executable path from the Codex config:

```toml
[mcp_servers.wolai-kb]
command = "C:\\Users\\lacus\\anaconda3\\Scripts\\wolai-mcp.exe"

[mcp_servers.wolai-kb.env]
WOLAI_APP_ID = "..."
WOLAI_APP_SECRET = "..."
WOLAI_ROOT_ID = "fND6EnXuZdoA1RPavzgaSY"
```

Never commit real credentials. Keep them only in the local Codex config or
another local secret store.

## Smoke Test

From this directory:

```powershell
C:\Users\lacus\anaconda3\python.exe .\scripts\wolai_mcp_client.py root
```

Expected shape:

```text
Current Root Directory: 'Starla / Sail (1/7, 14.28%)' (ID: fND6EnXuZdoA1RPavzgaSY)
```

List root children:

```powershell
C:\Users\lacus\anaconda3\python.exe .\scripts\wolai_mcp_client.py children
```

Read a page:

```powershell
C:\Users\lacus\anaconda3\python.exe .\scripts\wolai_mcp_client.py page fND6EnXuZdoA1RPavzgaSY
```

Read a page and expand Wolai reference blocks:

```powershell
C:\Users\lacus\anaconda3\python.exe .\scripts\wolai_mcp_client.py page-expanded rT8wZuoL6GKvVLi1VEP1SS --max-depth 4
```

This follows `[reference]` blocks through `source_block_id` and inline
`bi_link` references through `block_id` when the target is a common body block
such as text, headings, lists, callouts, quotes, equations, code, or another
reference. Page, database, image, and other container/media targets are noted
but not expanded. It keeps a visited set and stops at the configured max depth
to avoid reference cycles.

Read database rows:

```powershell
C:\Users\lacus\anaconda3\python.exe .\scripts\wolai_mcp_client.py database mjrgyUWX1NnNYmcHq2vMJ4
```

Print raw database JSON:

```powershell
C:\Users\lacus\anaconda3\python.exe .\scripts\wolai_mcp_client.py database mjrgyUWX1NnNYmcHq2vMJ4 --raw
```

Search page titles:

```powershell
C:\Users\lacus\anaconda3\python.exe .\scripts\wolai_mcp_client.py search Amazon --max-depth 2
```

## What Was Verified

- The installed `wolai-mcp` command is a stdio MCP server.
- MCP initialization succeeds through Python's `mcp.client.stdio` client.
- Tool listing succeeds.
- `get_root_info` succeeds.
- `list_child_blocks` succeeds on the configured root.
- `get_page_content` succeeds when stdout is set to UTF-8.
- `search_pages_by_title` succeeds for ASCII queries.
- Wolai database rows can be read through `GET /v1/databases/{id}`. The helper
  script exposes this as the `database` command and prints row `page_id` values.
- Wolai reference blocks can hide answer content from MCP's simplified page
  rendering. The helper script exposes `page-expanded` to recursively resolve
  `source_block_id` and eligible inline `bi_link` references with cycle
  protection.
