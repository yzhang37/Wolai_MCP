# Wolai MCP Agent Notes

This directory documents the repo-level Wolai MCP workflow for future
Codex/agent sessions. Treat these notes as operational memory, but keep them
machine-neutral: personal paths and real credentials belong only in local
Codex config files.

## Setup Contract

- MCP server name in Codex config: `wolai-kb`
- Default Codex config path: `CODEX_CONFIG` when set, otherwise
  `~/.codex/config.toml`
- Package/runtime: `wolai-mcp==1.1.0` installed in any local Python environment
- Repo-local replacement server: `scripts/wolai_mcp_plus.py`
- MCP implementation: stdio server built with `mcp.server.fastmcp.FastMCP`
- Server display name: `wolai-knowledge-base`
- Required Wolai env values: `WOLAI_APP_ID`, `WOLAI_APP_SECRET`,
  `WOLAI_ROOT_ID`

The local Codex config should point `command` to that machine's
`wolai-mcp` executable. Prefer an absolute path in the local config, but do not
commit machine-specific paths.

Generic local config shape:

```toml
[mcp_servers.wolai-kb]
command = "/absolute/path/to/Wolai_MCP/.venv/bin/wolai-mcp"

[mcp_servers.wolai-kb.env]
WOLAI_APP_ID = "..."
WOLAI_APP_SECRET = "..."
WOLAI_ROOT_ID = "fND6EnXuZdoA1RPavzgaSY"
```

Windows venv command shape:

```toml
command = "C:\\absolute\\path\\to\\Wolai_MCP\\.venv\\Scripts\\wolai-mcp.exe"
```

Preferred repo-local replacement server shape:

```toml
[mcp_servers.wolai-kb]
command = "/absolute/path/to/Wolai_MCP/.venv/bin/python"
args = ["/absolute/path/to/Wolai_MCP/scripts/wolai_mcp_plus.py"]

[mcp_servers.wolai-kb.env]
WOLAI_APP_ID = "..."
WOLAI_APP_SECRET = "..."
WOLAI_ROOT_ID = "fND6EnXuZdoA1RPavzgaSY"
```

Do not print or write `WOLAI_APP_SECRET` into logs, docs, commits, or chat
unless the user explicitly requests secret inspection. Prefer reading env
values from `config.toml` at runtime.

## Known Working Root

The configured root page was verified through the MCP server:

```text
Current Root Directory: 'Starla / Sail (1/7, 14.28%)' (ID: fND6EnXuZdoA1RPavzgaSY)
```

This confirms that the Wolai App ID/App Secret/root ID combination works when
the process can access `https://openapi.wolai.com`.

## Available Wolai MCP Tools

Read-oriented tools:

- `get_wolai_config`: show config status, masking secret presence.
- `get_root_info`: read the configured root page title and ID.
- `list_child_blocks`: list one page of immediate child blocks/pages for a
  block ID, returning `has_more` and `next_cursor` when more children exist.
- `get_page_content`: read a page/block and render child blocks as text.
- `search_pages_by_title`: traverse page tree and match page titles.
- `get_breadcrumbs`: trace a block path back through page hierarchy.

Repo-local Wolai MCP Plus read tools:

- `get_block_raw`: return raw block JSON for unknown block types.
- `read_block`: render pages/blocks with separate child/reference/inline
  expansion controls.
- `get_database_rows`: read one page of database rows and row page IDs,
  returning `has_more` and `next_cursor` when more rows exist.
- `search_tree`: search pages, blocks, and database row titles by traversing
  from a root.
- `get_api_capabilities`: explain implemented tools and public OpenAPI limits.
- `list_available_tools`: plain-language tool list for agents that cannot
  inspect MCP schemas.

Configuration tools:

- `set_wolai_credentials`: set Wolai App ID/App Secret.
- `set_root_page`: set the root page ID.
- Do not use configuration tools unless the user explicitly asks to edit Wolai
  MCP runtime config; do not print secrets.

Database note:

- The current `wolai-mcp` server does not expose a database query tool.
- Wolai database blocks are listed as `[database]` blocks, but row pages are
  not returned by `list_child_blocks`.
- Use Wolai OpenAPI `GET /v1/databases/{id}` to list database rows. Each row
  includes a `page_id`; read that row page with `get_page_content`.
- Wolai paginates batch reads with `page_size` (max 200) and `start_cursor`.
  MCP Plus exposes this as `page_size` and `cursor`; if `has_more=true`, call
  the same tool again with `cursor=next_cursor`.
- Public OpenAPI does not let `POST /blocks` create a `database` block. Use an
  existing database ID for database-row tests or writes.
- `create_database_rows` creates database row pages and returns their page IDs.
  Wolai public docs show empty row objects; cell-value writes are not documented
  and were observed to be ignored by the API.
- `scripts/wolai_mcp_client.py database <database_id>` wraps this read-only
  database call using credentials from Codex config without printing secrets.

Block reference rules:

- Some Wolai pages store real answer text in `[reference]` blocks. The public
  package's simplified `get_page_content` output may show them as `(Empty)`.
- Prefer `read_block` from Wolai MCP Plus when available.
- For a block-level `[reference]`, read the reference block's raw OpenAPI JSON,
  find `source_block_id`, then read that source block as the real content.
- For inline rich-text links, inspect entries with `type: "bi_link"` and
  `block_id`. Read the target block only after checking its type.
- Inline `bi_link.block_id` should normally be expanded only when the target is
  a common body element: text, heading, list, numbered list, formula/equation,
  callout, quote, code, or another reference. Use `expand_inline=all` only when
  the caller explicitly wants to chase page/database links.
- If an inline `bi_link.block_id` target is a page, database, image, media, or
  other container/asset block, normally do not expand it. Treat it as a link or
  source pointer.
- Always keep a per-read `visited` set and stop if a block ID repeats. Reference
  chains can form loops. Use path-based cycle detection where possible so the
  same block can still be rendered from independent branches.
- Prefer targeted block-by-block resolution over blindly expanding an entire
  page. In Wolai MCP Plus, tune `child_depth`, `reference_depth`,
  `inline_depth`, `expand_inline`, `expand_children`, `database_page_depth`, and
  `request_budget` per task.
- A helper may cache block JSON only within a single command invocation to avoid
  duplicate reads. Do not persist cache across commands; a new command should
  re-read Wolai because page content may change.

Write tools:

- `create_page`: create a subpage.
- `add_block`: append text/list/heading/etc. blocks.
- `add_code_block`: append a code block.
- MCP Plus write helpers split create payloads into Wolai's 20 records/request
  limit and return chunk metadata.
- MCP Plus accepts create-side aliases for common agent names:
  `bulleted_list -> bull_list`, `numbered_list -> enum_list`, and
  `equation -> block_equation`.

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
- Wolai's documented API limit is 5 requests/second per user. MCP Plus enforces
  a lightweight in-process throttle and still keeps request budgets on recursive
  reads.
- Avoid passing Chinese text through PowerShell here-strings unless stdout and
  source encoding are controlled. ASCII queries such as `Amazon` are safer for
  smoke tests.

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

For MCP Plus pagination:

1. Start with `page_size=100` or `page_size=200` depending on how much context
   the task needs.
2. Treat `has_more=false` as complete for that list.
3. If `has_more=true`, call the same tool again with `cursor=next_cursor`.
4. Do not assume a missing `total_count` means zero or complete; Wolai may omit
   total counts.

For write tasks:

1. Confirm the exact target page ID and intended change with the user.
2. Prefer `create_page`, `add_block`, or `add_code_block` over direct API calls.
3. Let MCP Plus chunk large creates into batches of 20, and report the resulting
   page/block IDs or chunk metadata after the write.

## Local Helper Script

Use `scripts/wolai_mcp_client.py` for smoke tests and direct MCP calls from
local Python when native Codex MCP tools are not visible. The helper defaults
to `CODEX_CONFIG` when set, otherwise `~/.codex/config.toml`; pass `--config`
only for nonstandard locations.

POSIX examples:

```bash
.venv/bin/python scripts/wolai_mcp_client.py doctor
.venv/bin/python scripts/wolai_mcp_client.py tools
.venv/bin/python scripts/wolai_mcp_client.py root
.venv/bin/python scripts/wolai_mcp_client.py children
.venv/bin/python scripts/wolai_mcp_client.py page fND6EnXuZdoA1RPavzgaSY
.venv/bin/python scripts/wolai_mcp_client.py page-expanded rT8wZuoL6GKvVLi1VEP1SS --max-depth 4
.venv/bin/python scripts/wolai_mcp_client.py database mjrgyUWX1NnNYmcHq2vMJ4
.venv/bin/python scripts/wolai_mcp_client.py search Amazon --max-depth 2
```

Windows venv examples:

```powershell
.\.venv\Scripts\python.exe .\scripts\wolai_mcp_client.py doctor
.\.venv\Scripts\python.exe .\scripts\wolai_mcp_client.py tools
.\.venv\Scripts\python.exe .\scripts\wolai_mcp_client.py root
.\.venv\Scripts\python.exe .\scripts\wolai_mcp_client.py children
.\.venv\Scripts\python.exe .\scripts\wolai_mcp_client.py page fND6EnXuZdoA1RPavzgaSY
.\.venv\Scripts\python.exe .\scripts\wolai_mcp_client.py page-expanded rT8wZuoL6GKvVLi1VEP1SS --max-depth 4
.\.venv\Scripts\python.exe .\scripts\wolai_mcp_client.py database mjrgyUWX1NnNYmcHq2vMJ4
.\.venv\Scripts\python.exe .\scripts\wolai_mcp_client.py search Amazon --max-depth 2
```
