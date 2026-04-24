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

Debug reference expansion for one page:

```powershell
C:\Users\lacus\anaconda3\python.exe .\scripts\wolai_mcp_client.py page-expanded rT8wZuoL6GKvVLi1VEP1SS --max-depth 4
```

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
  rendering. Resolve block-level references via `source_block_id`, and resolve
  inline `bi_link.block_id` only when the target is a normal body block.
