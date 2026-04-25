from __future__ import annotations

import json
import os
import sys
import time
from dataclasses import dataclass, field
from typing import Any

import requests
from mcp.server.fastmcp import FastMCP


BASE_URL = os.environ.get("WOLAI_BASE_URL", "https://openapi.wolai.com/v1")
SERVER_NAME = os.environ.get("WOLAI_MCP_SERVER_NAME", "wolai-knowledge-base-plus")

BODY_BLOCK_TYPES = {
    "text",
    "heading",
    "heading_1",
    "heading_2",
    "heading_3",
    "enum_list",
    "bull_list",
    "bulleted_list",
    "numbered_list",
    "todo_list",
    "todo_list_pro",
    "toggle_list",
    "quote",
    "callout",
    "reference",
    "block_equation",
    "code",
    "python",
    "javascript",
    "java",
}

CONTAINER_BLOCK_TYPES = BODY_BLOCK_TYPES | {"page", "row"}
MEDIA_OR_EXTERNAL_TYPES = {
    "database",
    "image",
    "video",
    "file",
    "bookmark",
    "pdf",
    "embed",
    "table",
}


class WolaiError(RuntimeError):
    pass


@dataclass
class RequestBudget:
    limit: int
    used: int = 0

    def take(self) -> None:
        self.used += 1
        if self.limit > 0 and self.used > self.limit:
            raise WolaiError(
                f"Request budget exceeded ({self.limit}). "
                "Increase request_budget or narrow expansion settings."
            )


@dataclass
class RenderPolicy:
    child_depth: int = 3
    reference_depth: int = 4
    inline_depth: int = 2
    expand_inline: str = "body"  # off, body, all, metadata
    expand_children: str = "body"  # off, body, all
    expand_databases: bool = True
    database_page_depth: int = 0
    show_ids: bool = True
    request_budget: int = 200
    max_output_chars: int = 30000

    @classmethod
    def from_args(
        cls,
        *,
        child_depth: int,
        reference_depth: int,
        inline_depth: int,
        expand_inline: str,
        expand_children: str,
        expand_databases: bool,
        database_page_depth: int,
        show_ids: bool,
        request_budget: int,
        max_output_chars: int,
    ) -> "RenderPolicy":
        policy = cls(
            child_depth=max(0, child_depth),
            reference_depth=max(0, reference_depth),
            inline_depth=max(0, inline_depth),
            expand_inline=expand_inline.lower(),
            expand_children=expand_children.lower(),
            expand_databases=expand_databases,
            database_page_depth=max(0, database_page_depth),
            show_ids=show_ids,
            request_budget=max(0, request_budget),
            max_output_chars=max(0, max_output_chars),
        )
        if policy.expand_inline not in {"off", "body", "all", "metadata"}:
            raise ValueError("expand_inline must be one of: off, body, all, metadata")
        if policy.expand_children not in {"off", "body", "all"}:
            raise ValueError("expand_children must be one of: off, body, all")
        return policy


@dataclass
class RenderState:
    budget: RequestBudget
    block_cache: dict[str, dict[str, Any]] = field(default_factory=dict)
    children_cache: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    database_cache: dict[str, dict[str, Any]] = field(default_factory=dict)


class WolaiClient:
    def __init__(self) -> None:
        self._token: str | None = None

    def app_id(self) -> str:
        value = os.environ.get("WOLAI_APP_ID", "")
        if not value:
            raise WolaiError("WOLAI_APP_ID is not set.")
        return value

    def app_secret(self) -> str:
        value = os.environ.get("WOLAI_APP_SECRET", "")
        if not value:
            raise WolaiError("WOLAI_APP_SECRET is not set.")
        return value

    def root_id(self) -> str:
        return os.environ.get("WOLAI_ROOT_ID", "")

    def token(self, *, force_refresh: bool = False) -> str:
        if self._token and not force_refresh:
            return self._token
        payload = {"appId": self.app_id(), "appSecret": self.app_secret()}
        response = requests.post(f"{BASE_URL}/token", json=payload, timeout=20)
        response.raise_for_status()
        data = response.json()
        token = data.get("data", {}).get("app_token")
        if not token:
            raise WolaiError(f"Token response did not include app_token: {data}")
        self._token = token
        return token

    def headers(self) -> dict[str, str]:
        return {"Authorization": self.token(), "Content-Type": "application/json"}

    def request_json(
        self,
        method: str,
        path: str,
        *,
        budget: RequestBudget | None = None,
        json_body: Any | None = None,
        retry: int = 3,
    ) -> dict[str, Any]:
        if budget is not None:
            budget.take()
        url = f"{BASE_URL}{path}"
        last_error: Exception | None = None
        for attempt in range(retry + 1):
            try:
                response = requests.request(
                    method,
                    url,
                    headers=self.headers(),
                    json=json_body,
                    timeout=30,
                )
                if response.status_code == 401 and attempt == 0:
                    self.token(force_refresh=True)
                    continue
                if response.status_code == 429 and attempt < retry:
                    wait = response.headers.get("Retry-After")
                    sleep_seconds = float(wait) if wait else min(2.0 * (attempt + 1), 6.0)
                    time.sleep(sleep_seconds)
                    continue
                response.raise_for_status()
                return response.json()
            except Exception as exc:  # noqa: BLE001 - return readable MCP errors.
                last_error = exc
                if attempt < retry:
                    time.sleep(min(1.0 * (attempt + 1), 3.0))
                    continue
                break
        raise WolaiError(f"{method} {path} failed: {last_error}") from last_error

    def get_block(self, block_id: str, state: RenderState | None = None) -> dict[str, Any]:
        if state and block_id in state.block_cache:
            return state.block_cache[block_id]
        data = self.request_json("GET", f"/blocks/{block_id}", budget=state.budget if state else None)
        block = data.get("data", {})
        if state is not None:
            state.block_cache[block_id] = block
        return block

    def get_children(self, block_id: str, state: RenderState | None = None) -> list[dict[str, Any]]:
        if state and block_id in state.children_cache:
            return state.children_cache[block_id]
        data = self.request_json(
            "GET",
            f"/blocks/{block_id}/children",
            budget=state.budget if state else None,
        )
        children = data.get("data", [])
        if state is not None:
            state.children_cache[block_id] = children
            for child in children:
                child_id = child.get("id")
                if child_id:
                    state.block_cache[child_id] = child
        return children

    def get_database(self, database_id: str, state: RenderState | None = None) -> dict[str, Any]:
        if state and database_id in state.database_cache:
            return state.database_cache[database_id]
        data = self.request_json(
            "GET",
            f"/databases/{database_id}",
            budget=state.budget if state else None,
        )
        payload = data.get("data", {})
        if state is not None:
            state.database_cache[database_id] = payload
        return payload

    def create_blocks(self, parent_id: str, blocks: list[dict[str, Any]]) -> dict[str, Any]:
        return self.request_json(
            "POST",
            "/blocks",
            json_body={"parent_id": parent_id, "blocks": blocks},
        )

    def create_database_rows(self, database_id: str, rows: list[dict[str, Any]]) -> dict[str, Any]:
        return self.request_json(
            "POST",
            f"/databases/{database_id}/rows",
            json_body={"rows": rows},
        )


client = WolaiClient()
mcp = FastMCP(SERVER_NAME)


def json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2)


def rich_text_to_text(content: Any) -> tuple[str, list[dict[str, str]]]:
    if not content:
        return "", []
    if isinstance(content, str):
        return content, []
    if isinstance(content, dict):
        content = [content]
    if not isinstance(content, list):
        return str(content), []

    parts: list[str] = []
    links: list[dict[str, str]] = []
    for item in content:
        if not isinstance(item, dict):
            parts.append(str(item))
            continue
        title = str(item.get("title", ""))
        if item.get("bold"):
            title = f"**{title}**"
        if item.get("italic"):
            title = f"*{title}*"
        if item.get("strikethrough"):
            title = f"~~{title}~~"
        parts.append(title)
        if item.get("type") == "bi_link" and item.get("block_id"):
            links.append(
                {
                    "label": str(item.get("title") or "linked block"),
                    "block_id": str(item["block_id"]),
                }
            )
    return "".join(parts), links


def block_title(block: dict[str, Any]) -> str:
    text, _links = rich_text_to_text(block.get("content", ""))
    return text or ""


def block_id_of(block: dict[str, Any], fallback: str = "") -> str:
    return str(block.get("id") or fallback)


def format_block_line(block: dict[str, Any], depth: int, policy: RenderPolicy) -> str:
    block_type = str(block.get("type", ""))
    block_id = block_id_of(block)
    text = block_title(block)
    suffix = f" (ID: {block_id})" if policy.show_ids and block_id else ""
    indent = "  " * depth

    if block_type == "divider":
        return f"{indent}---{suffix}"
    if block_type == "page":
        return f"{indent}# {text or '(Untitled)'}{suffix}"
    if block_type in {"heading", "heading_1"}:
        return f"{indent}## {text}{suffix}"
    if block_type == "heading_2":
        return f"{indent}### {text}{suffix}"
    if block_type == "heading_3":
        return f"{indent}#### {text}{suffix}"
    if block_type in {"enum_list", "bulleted_list"}:
        return f"{indent}- {text}{suffix}"
    if block_type in {"bull_list", "numbered_list"}:
        return f"{indent}1. {text}{suffix}"
    if block_type in {"todo_list", "todo_list_pro"}:
        checked = "x" if block.get("checked") else " "
        return f"{indent}- [{checked}] {text}{suffix}"
    if block_type == "toggle_list":
        return f"{indent}> {text}{suffix}"
    if block_type == "quote":
        return f"{indent}> {text}{suffix}"
    if block_type == "code":
        language = block.get("language") or ""
        return f"{indent}```{language}\n{text}\n{indent}```{suffix}"
    label = block_type or "unknown"
    return f"{indent}[{label}] {text or '(Empty)'}{suffix}"


def should_expand_children(block_type: str, policy: RenderPolicy) -> bool:
    if policy.expand_children == "off":
        return False
    if policy.expand_children == "all":
        return True
    return block_type in CONTAINER_BLOCK_TYPES


def should_expand_inline(block_type: str, policy: RenderPolicy) -> bool:
    if policy.expand_inline == "off":
        return False
    if policy.expand_inline in {"all", "metadata"}:
        return True
    return block_type in BODY_BLOCK_TYPES


def format_database_rows(
    database_id: str,
    *,
    state: RenderState,
    policy: RenderPolicy,
    depth: int,
) -> list[str]:
    indent = "  " * depth
    data = client.get_database(database_id, state)
    rows = data.get("rows", [])
    if not rows:
        return [f"{indent}[database rows] No rows found."]
    lines = [f"{indent}[database rows] {len(rows)} row(s)"]
    for index, row in enumerate(rows, 1):
        page_id = row.get("page_id", "")
        row_data = row.get("data", {})
        fields: list[str] = []
        title = ""
        if isinstance(row_data, dict):
            for key, cell in row_data.items():
                value = cell.get("value", "") if isinstance(cell, dict) else cell
                if value in ("", None, []):
                    continue
                if isinstance(cell, dict) and cell.get("type") == "primary":
                    title = str(value)
                else:
                    fields.append(f"{key}={value}")
        suffix = f" | {'; '.join(fields)}" if fields else ""
        page_part = f" page_id={page_id}" if page_id else ""
        lines.append(f"{indent}- {index}. {title or '(Untitled)'}{page_part}{suffix}")
        if page_id and policy.database_page_depth > 0:
            lines.extend(
                render_block(
                    page_id,
                    state=state,
                    policy=policy,
                    depth=depth + 1,
                    child_remaining=policy.database_page_depth,
                    reference_remaining=policy.reference_depth,
                    inline_remaining=policy.inline_depth,
                    path=(),
                )
            )
    return lines


def render_block(
    block_id: str,
    *,
    state: RenderState,
    policy: RenderPolicy,
    depth: int,
    child_remaining: int,
    reference_remaining: int,
    inline_remaining: int,
    path: tuple[str, ...],
) -> list[str]:
    indent = "  " * depth
    if block_id in path:
        return [f"{indent}[cycle skipped: {block_id}]"]

    block = client.get_block(block_id, state)
    block_type = str(block.get("type", ""))
    current_path = path + (block_id,)

    if block_type == "reference":
        source_id = block.get("source_block_id")
        if not source_id:
            return [f"{indent}[reference unresolved] {block_id}"]
        lines = [f"{indent}[reference -> {source_id}]"]
        if reference_remaining <= 0:
            lines.append(f"{indent}  [reference depth exhausted]")
            return lines
        lines.extend(
            render_block(
                str(source_id),
                state=state,
                policy=policy,
                depth=depth + 1,
                child_remaining=child_remaining,
                reference_remaining=reference_remaining - 1,
                inline_remaining=inline_remaining,
                path=current_path,
            )
        )
        return lines

    lines = [format_block_line(block, depth, policy)]
    text, inline_links = rich_text_to_text(block.get("content", ""))

    if inline_links and policy.expand_inline != "off":
        for link in inline_links:
            linked_id = link["block_id"]
            linked_block = client.get_block(linked_id, state)
            linked_type = str(linked_block.get("type", ""))
            lines.append(
                f"{indent}  [bi_link: {link['label']} -> {linked_id}, type={linked_type or 'unknown'}]"
            )
            if policy.expand_inline == "metadata":
                continue
            if inline_remaining <= 0:
                lines.append(f"{indent}    [inline depth exhausted]")
                continue
            if not should_expand_inline(linked_type, policy):
                lines.append(f"{indent}    [inline target not expanded]")
                continue
            lines.extend(
                render_block(
                    linked_id,
                    state=state,
                    policy=policy,
                    depth=depth + 2,
                    child_remaining=max(0, child_remaining - 1),
                    reference_remaining=reference_remaining,
                    inline_remaining=inline_remaining - 1,
                    path=current_path,
                )
            )

    if block_type == "database" and policy.expand_databases:
        lines.extend(format_database_rows(block_id, state=state, policy=policy, depth=depth + 1))

    if child_remaining > 0 and should_expand_children(block_type, policy):
        for child in client.get_children(block_id, state):
            child_id = child.get("id")
            if not child_id:
                continue
            lines.extend(
                render_block(
                    str(child_id),
                    state=state,
                    policy=policy,
                    depth=depth + 1,
                    child_remaining=child_remaining - 1,
                    reference_remaining=reference_remaining,
                    inline_remaining=inline_remaining,
                    path=current_path,
                )
            )
    if not text and not inline_links:
        return lines
    return lines


def truncate_text(text: str, max_chars: int) -> str:
    if max_chars and len(text) > max_chars:
        return text[:max_chars] + "\n\n...[truncated]"
    return text


def parse_blocks_json(blocks_json: str) -> list[dict[str, Any]]:
    try:
        blocks = json.loads(blocks_json)
    except json.JSONDecodeError as exc:
        raise ValueError(f"blocks_json is not valid JSON: {exc}") from exc
    if isinstance(blocks, dict):
        blocks = [blocks]
    if not isinstance(blocks, list) or not all(isinstance(block, dict) for block in blocks):
        raise ValueError("blocks_json must be a JSON object or array of objects.")
    return blocks


def simple_block(content: str, block_type: str = "text", *, language: str = "") -> dict[str, Any]:
    block: dict[str, Any] = {"type": block_type}
    if block_type != "divider":
        block["content"] = [{"title": content}]
    if block_type == "heading":
        block["level"] = 1
    if block_type == "code" and language:
        block["language"] = language
    return block


@mcp.tool()
def get_wolai_config() -> str:
    """Show config status without printing secrets."""
    app_id = os.environ.get("WOLAI_APP_ID", "")
    app_secret = os.environ.get("WOLAI_APP_SECRET", "")
    root_id = os.environ.get("WOLAI_ROOT_ID", "")
    return "\n".join(
        [
            "Wolai MCP Plus configuration",
            f"server: {SERVER_NAME}",
            f"base_url: {BASE_URL}",
            f"WOLAI_APP_ID: {'present' if app_id else 'missing'}",
            f"WOLAI_APP_SECRET: {'present' if app_secret else 'missing'}",
            f"WOLAI_ROOT_ID: {root_id or 'missing'}",
        ]
    )


@mcp.tool()
def get_api_capabilities() -> str:
    """Describe implemented tools and known Wolai OpenAPI limits."""
    return (
        "Implemented: token auth, block raw/detail, child listing, page rendering, "
        "reference expansion, bi_link expansion, database row listing, tree search, "
        "block/page creation, code blocks, and database row creation.\n"
        "Known Wolai OpenAPI limit: public docs currently expose only Token, "
        "Block create/detail/children, Database get/create rows. Block update/delete "
        "and database row update/delete are not advertised, so this server does not "
        "pretend they are safely supported."
    )


@mcp.tool()
def list_available_tools() -> str:
    """
    Return a plain-language tool list for agents that cannot inspect MCP schemas.
    """
    return """Wolai MCP Plus tools

Read/config:
- get_wolai_config()
  Show config status without printing secrets.
- get_api_capabilities()
  Explain implemented capabilities and known public Wolai OpenAPI limits.
- list_available_tools()
  Print this plain-language tool list.
- get_root_info()
  Read the configured root page title and ID.
- get_block_raw(block_id)
  Return raw OpenAPI JSON for a block. Use this for unknown block types.
- list_child_blocks(block_id="")
  List immediate child blocks. Defaults to WOLAI_ROOT_ID.

Rich read:
- read_block(block_id="", child_depth=3, reference_depth=4, inline_depth=2,
             expand_inline="body", expand_children="body",
             expand_databases=True, database_page_depth=0,
             show_ids=True, request_budget=200, max_output_chars=30000)
  Render a page/block with flexible expansion.
  expand_inline: off | metadata | body | all.
  expand_children: off | body | all.
  Use this instead of the public package's get_page_content when a page contains
  reference blocks, bi_link inline references, toggles, or databases.

Database/search:
- get_database_rows(database_id, include_page_content=False,
                    page_depth=1, max_output_chars=30000)
  Read database rows and row page IDs. Can optionally render each row page.
- search_tree(query, start_id="", max_depth=3, include_blocks=True,
              include_databases=True, request_budget=250)
  Traverse from a root and search page/block titles plus database row titles.

Write:
- create_page(title, parent_id="")
  Create a child page under parent_id, or under WOLAI_ROOT_ID if omitted.
- add_text_blocks(parent_id, content, block_type="text")
  Append plain text lines as simple blocks.
- add_code_block(parent_id, code, language="python")
  Append one code block.
- add_blocks(parent_id, blocks_json)
  Append raw Wolai block JSON for advanced cases.
- create_database_rows(database_id, rows_json)
  Create database rows with raw Wolai row JSON.

Safety notes:
- The server never prints WOLAI_APP_SECRET.
- read_block has request_budget and basic 429 retry/backoff.
- Block update/delete and database row update/delete are not exposed because
  they are not clearly advertised in the public Wolai OpenAPI docs."""


@mcp.tool()
def get_root_info() -> str:
    """Read the configured root page title and ID."""
    root_id = client.root_id()
    if not root_id:
        return "WOLAI_ROOT_ID is not set."
    try:
        block = client.get_block(root_id)
        return f"Current Root Directory: '{block_title(block)}' (ID: {root_id})"
    except Exception as exc:  # noqa: BLE001
        return f"Error reading root {root_id}: {exc}"


@mcp.tool()
def get_block_raw(block_id: str) -> str:
    """Return raw OpenAPI JSON for a block. Useful for unknown block types."""
    try:
        return json_dumps(client.get_block(block_id))
    except Exception as exc:  # noqa: BLE001
        return f"Error reading block {block_id}: {exc}"


@mcp.tool()
def list_child_blocks(block_id: str = "") -> str:
    """List immediate child blocks for a page/block."""
    block_id = block_id or client.root_id()
    if not block_id:
        return "No block_id provided and WOLAI_ROOT_ID is not set."
    try:
        children = client.get_children(block_id)
    except Exception as exc:  # noqa: BLE001
        return f"Error listing children for {block_id}: {exc}"
    if not children:
        return "No children found."
    lines = []
    for child in children:
        child_type = child.get("type", "unknown")
        title = block_title(child) or "(Empty)"
        lines.append(f"- [{child_type}] {title} (ID: {child.get('id', '')})")
    return "\n".join(lines)


@mcp.tool()
def read_block(
    block_id: str = "",
    child_depth: int = 3,
    reference_depth: int = 4,
    inline_depth: int = 2,
    expand_inline: str = "body",
    expand_children: str = "body",
    expand_databases: bool = True,
    database_page_depth: int = 0,
    show_ids: bool = True,
    request_budget: int = 200,
    max_output_chars: int = 30000,
) -> str:
    """
    Render a block/page with flexible expansion.

    expand_inline: off, metadata, body, all.
    expand_children: off, body, all.
    Depth knobs are independent: child_depth, reference_depth, inline_depth.
    """
    block_id = block_id or client.root_id()
    if not block_id:
        return "No block_id provided and WOLAI_ROOT_ID is not set."
    try:
        policy = RenderPolicy.from_args(
            child_depth=child_depth,
            reference_depth=reference_depth,
            inline_depth=inline_depth,
            expand_inline=expand_inline,
            expand_children=expand_children,
            expand_databases=expand_databases,
            database_page_depth=database_page_depth,
            show_ids=show_ids,
            request_budget=request_budget,
            max_output_chars=max_output_chars,
        )
        state = RenderState(budget=RequestBudget(policy.request_budget))
        lines = render_block(
            block_id,
            state=state,
            policy=policy,
            depth=0,
            child_remaining=policy.child_depth,
            reference_remaining=policy.reference_depth,
            inline_remaining=policy.inline_depth,
            path=(),
        )
        header = f"[requests used: {state.budget.used}/{policy.request_budget or 'unlimited'}]"
        return truncate_text(header + "\n" + "\n".join(lines), policy.max_output_chars)
    except Exception as exc:  # noqa: BLE001
        return f"Error rendering {block_id}: {exc}"


@mcp.tool()
def get_database_rows(
    database_id: str,
    include_page_content: bool = False,
    page_depth: int = 1,
    max_output_chars: int = 30000,
) -> str:
    """Read a Wolai database and list rows, including row page IDs."""
    try:
        policy = RenderPolicy(child_depth=page_depth, max_output_chars=max_output_chars)
        state = RenderState(budget=RequestBudget(200))
        data = client.get_database(database_id, state)
        rows = data.get("rows", [])
        lines = [f"Database {database_id}: {len(rows)} row(s)"]
        for index, row in enumerate(rows, 1):
            page_id = row.get("page_id", "")
            row_data = row.get("data", {})
            fields = []
            title = ""
            if isinstance(row_data, dict):
                for key, cell in row_data.items():
                    value = cell.get("value", "") if isinstance(cell, dict) else cell
                    if value in ("", None, []):
                        continue
                    if isinstance(cell, dict) and cell.get("type") == "primary":
                        title = str(value)
                    else:
                        fields.append(f"{key}={value}")
            suffix = f" | {'; '.join(fields)}" if fields else ""
            lines.append(f"- {index}. {title or '(Untitled)'} (page_id: {page_id}){suffix}")
            if include_page_content and page_id:
                lines.extend(
                    render_block(
                        str(page_id),
                        state=state,
                        policy=policy,
                        depth=1,
                        child_remaining=page_depth,
                        reference_remaining=policy.reference_depth,
                        inline_remaining=policy.inline_depth,
                        path=(),
                    )
                )
        return truncate_text("\n".join(lines), max_output_chars)
    except Exception as exc:  # noqa: BLE001
        return f"Error reading database {database_id}: {exc}"


@mcp.tool()
def search_tree(
    query: str,
    start_id: str = "",
    max_depth: int = 3,
    include_blocks: bool = True,
    include_databases: bool = True,
    request_budget: int = 250,
) -> str:
    """Search page/block titles and database row titles by traversing from a root."""
    start_id = start_id or client.root_id()
    if not start_id:
        return "No start_id provided and WOLAI_ROOT_ID is not set."
    state = RenderState(budget=RequestBudget(request_budget))
    query_lower = query.lower()
    results: list[str] = []
    queue: list[tuple[str, int]] = [(start_id, 0)]
    seen: set[str] = set()
    try:
        while queue:
            current_id, depth = queue.pop(0)
            if current_id in seen or depth > max_depth:
                continue
            seen.add(current_id)
            block = client.get_block(current_id, state)
            block_type = str(block.get("type", ""))
            title = block_title(block)
            if query_lower in title.lower():
                results.append(f"- [{block_type}] {title} (ID: {current_id}, depth={depth})")
            if block_type == "database" and include_databases:
                data = client.get_database(current_id, state)
                for row in data.get("rows", []):
                    row_title = ""
                    row_data = row.get("data", {})
                    if isinstance(row_data, dict):
                        for cell in row_data.values():
                            if isinstance(cell, dict) and cell.get("type") == "primary":
                                row_title = str(cell.get("value", ""))
                                break
                    if query_lower in row_title.lower():
                        results.append(
                            f"- [database-row] {row_title} "
                            f"(page_id: {row.get('page_id', '')}, database={current_id})"
                        )
            if depth < max_depth:
                for child in client.get_children(current_id, state):
                    child_id = child.get("id")
                    child_type = str(child.get("type", ""))
                    if child_id and (include_blocks or child_type in {"page", "database"}):
                        queue.append((str(child_id), depth + 1))
        if not results:
            return f"No matches for '{query}' within depth {max_depth} of {start_id}."
        return "\n".join(results)
    except Exception as exc:  # noqa: BLE001
        partial = "\n".join(results)
        suffix = f"\n\n[stopped early: {exc}]" if partial else f"Search error: {exc}"
        return partial + suffix


@mcp.tool()
def create_page(title: str, parent_id: str = "") -> str:
    """Create a child page under parent_id, or under WOLAI_ROOT_ID if omitted."""
    parent_id = parent_id or client.root_id()
    if not parent_id:
        return "No parent_id provided and WOLAI_ROOT_ID is not set."
    try:
        response = client.create_blocks(parent_id, [simple_block(title, "page")])
        return json_dumps(response)
    except Exception as exc:  # noqa: BLE001
        return f"Error creating page: {exc}"


@mcp.tool()
def add_blocks(parent_id: str, blocks_json: str) -> str:
    """
    Append raw Wolai block objects to a parent.

    blocks_json may be one block object or a list of block objects.
    """
    try:
        blocks = parse_blocks_json(blocks_json)
        response = client.create_blocks(parent_id, blocks)
        return json_dumps(response)
    except Exception as exc:  # noqa: BLE001
        return f"Error adding blocks: {exc}"


@mcp.tool()
def add_text_blocks(parent_id: str, content: str, block_type: str = "text") -> str:
    """Append plain text lines as simple Wolai blocks."""
    try:
        if block_type == "divider":
            blocks = [simple_block("", "divider")]
        else:
            blocks = [simple_block(line, block_type) for line in content.splitlines() if line.strip()]
        if not blocks:
            return "No content provided."
        response = client.create_blocks(parent_id, blocks)
        return json_dumps(response)
    except Exception as exc:  # noqa: BLE001
        return f"Error adding text blocks: {exc}"


@mcp.tool()
def add_code_block(parent_id: str, code: str, language: str = "python") -> str:
    """Append a code block."""
    try:
        response = client.create_blocks(parent_id, [simple_block(code, "code", language=language)])
        return json_dumps(response)
    except Exception as exc:  # noqa: BLE001
        return f"Error adding code block: {exc}"


@mcp.tool()
def create_database_rows(database_id: str, rows_json: str) -> str:
    """
    Create database rows using raw Wolai row JSON.

    rows_json must be a row object or a list of row objects matching Wolai OpenAPI.
    """
    try:
        rows = json.loads(rows_json)
        if isinstance(rows, dict):
            rows = [rows]
        if not isinstance(rows, list) or not all(isinstance(row, dict) for row in rows):
            return "rows_json must be a JSON object or array of row objects."
        response = client.create_database_rows(database_id, rows)
        return json_dumps(response)
    except Exception as exc:  # noqa: BLE001
        return f"Error creating database rows: {exc}"


def main() -> None:
    try:
        mcp.run()
    except KeyboardInterrupt:
        print("Stopped.", file=sys.stderr)


if __name__ == "__main__":
    main()
