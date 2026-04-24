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

Block reference rules:

- Some Wolai pages store real answer text in `[reference]` blocks. MCP's
  simplified `get_page_content` output may show them as `(Empty)`.
- For a block-level `[reference]`, read the reference block's raw OpenAPI JSON,
  find `source_block_id`, then read that source block as the real content.
- For inline rich-text links, inspect entries with `type: "bi_link"` and
  `block_id`. Read the target block only after checking its type.
- Inline `bi_link.block_id` should be expanded only when the target is a common
  body element: text, heading, list, numbered list, formula/equation, callout,
  quote, code, or another reference.
- If an inline `bi_link.block_id` target is a page, database, image, media, or
  other container/asset block, normally do not expand it. Treat it as a link or
  source pointer.
- Always keep a per-read `visited` set and stop if a block ID repeats. Reference
  chains can form loops. Also use a small max depth.
- Prefer targeted block-by-block resolution over blindly expanding an entire
  page. `scripts/wolai_mcp_client.py page-expanded <block_id>` is only a
  debugging convenience, not the default reading strategy.
- A helper may cache block JSON only within a single command invocation to avoid
  duplicate reads. Do not persist cache across commands; a new command should
  re-read Wolai because page content may change.

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
5. If the page contains `[reference]` blocks, resolve each reference by reading
   its raw `source_block_id` and then that source block.
6. If a text block contains inline `bi_link` entries, inspect the target block
   type. Expand only body-element targets; skip page/database/media targets.
7. Use `search_pages_by_title` only when navigation is not enough.

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
