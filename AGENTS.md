# Wolai MCP Agent Notes

This directory documents the working Wolai MCP setup for this machine.
It is meant for future Codex/agent sessions, so the notes below should be
treated as operational memory.

## Current Setup

- MCP server name in Codex config: `wolai-kb`
- Codex config path: `C:\Users\lacus\.codex\config.toml`
- Wolai MCP executable: `C:\Users\lacus\anaconda3\Scripts\wolai-mcp.exe`
- Python runtime used by the MCP package: `C:\Users\lacus\anaconda3\python.exe`
- Installed package: `wolai-mcp` version `1.1.0`
- MCP implementation: stdio server built with `mcp.server.fastmcp.FastMCP`
- Server display name: `wolai-knowledge-base`

Do not print or write `WOLAI_APP_SECRET` into logs, docs, commits, or chat
unless the user explicitly requests secret inspection. Prefer reading env
values from `config.toml` at runtime.

## Known Working Root

The configured root page was verified through the MCP server:

```text
Current Root Directory: 'Starla / Sail (1/7, 14.28%)' (ID: fND6EnXuZdoA1RPavzgaSY)
```

This confirms that the Wolai App ID/App Secret/root ID combination works
when the process can access `https://openapi.wolai.com`.

## Available Wolai MCP Tools

Read-oriented tools:

- `get_wolai_config`: show config status, masking secret presence.
- `get_root_info`: read the configured root page title and ID.
- `list_child_blocks`: list immediate child blocks/pages for a block ID.
- `get_page_content`: read a page/block and render child blocks as text.
- `search_pages_by_title`: traverse page tree and match page titles.
- `get_breadcrumbs`: trace a block path back through page hierarchy.

Database note:

- The current `wolai-mcp` server does not expose a database query tool.
- Wolai database blocks are listed as `[database]` blocks, but row pages are
  not returned by `list_child_blocks`.
- Use Wolai OpenAPI `GET /v1/databases/{id}` to list database rows. Each row
  includes a `page_id`; read that row page with `get_page_content`.
- `scripts/wolai_mcp_client.py database <database_id>` wraps this read-only
  database call using credentials from Codex config without printing secrets.

Reference-block note:

- Some Wolai pages store the real answer body in `[reference]` blocks. The
  MCP-rendered `get_page_content` output may show these as `(Empty)`.
- Raw OpenAPI blocks expose `source_block_id` for these references. Read that
  source block to get the actual content.
- Wolai rich text can also contain inline `bi_link` references with a
  `block_id`. These may point to pages or blocks that need another read.
- Always use cycle detection and a max depth when expanding references, because
  references can point back to already visited content.
- `scripts/wolai_mcp_client.py page-expanded <block_id>` expands reference
  blocks and inline `bi_link` references recursively with a visited set.

Write tools:

- `create_page`: create a subpage.
- `add_block`: append text/list/heading/etc. blocks.
- `add_code_block`: append a code block.

Unless the user asks to edit Wolai, default to the read-only tools.

## Important Behavior

- Codex does not always hot-load newly configured MCP servers into the native
  tool list for an existing conversation. A restart/new session may be needed.
- Even if Codex does not expose `mcp__wolai...` tools directly, the server can
  be called with the Python MCP client using stdio.
- In sandboxed commands, network access to `openapi.wolai.com` may fail with a
  proxy/refused connection. Retry the exact read-only MCP test with escalated
  network permission if the task requires verification.
- On Windows console output, set stdout to UTF-8 before printing Wolai page
  content, because Wolai pages may contain emoji or non-GBK text.
- `search_pages_by_title` can be slow because Wolai does not expose a global
  search API; the MCP server traverses the tree from the root.
- Avoid passing Chinese text through PowerShell here-strings unless stdout and
  source encoding are controlled. ASCII queries such as `Amazon` worked in the
  previous smoke test.

## Preferred Workflow

For read tasks:

1. Call `get_root_info` to verify auth and the root page.
2. Use `list_child_blocks` on the root or known page ID to navigate.
3. If a child is a `[database]`, use the helper script's `database` command to
   list row page IDs.
4. Use `get_page_content` for simple pages.
5. If the page contains `[reference]` blocks or inline linked blocks, use
   `page-expanded` so referenced answer sections are not missed.
6. Use `search_pages_by_title` only when navigation is not enough.

For write tasks:

1. Confirm the exact target page ID and intended change with the user.
2. Prefer `create_page`, `add_block`, or `add_code_block` over direct API calls.
3. Report the resulting page/block ID after the write.

## Local Helper Script

Use `scripts/wolai_mcp_client.py` for smoke tests and direct MCP calls from
local Python when native Codex MCP tools are not visible.

Examples:

```powershell
C:\Users\lacus\anaconda3\python.exe .\scripts\wolai_mcp_client.py tools
C:\Users\lacus\anaconda3\python.exe .\scripts\wolai_mcp_client.py root
C:\Users\lacus\anaconda3\python.exe .\scripts\wolai_mcp_client.py children
C:\Users\lacus\anaconda3\python.exe .\scripts\wolai_mcp_client.py page fND6EnXuZdoA1RPavzgaSY
C:\Users\lacus\anaconda3\python.exe .\scripts\wolai_mcp_client.py page-expanded rT8wZuoL6GKvVLi1VEP1SS --max-depth 4
C:\Users\lacus\anaconda3\python.exe .\scripts\wolai_mcp_client.py database mjrgyUWX1NnNYmcHq2vMJ4
C:\Users\lacus\anaconda3\python.exe .\scripts\wolai_mcp_client.py search Amazon --max-depth 2
```
