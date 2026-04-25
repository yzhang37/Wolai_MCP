from __future__ import annotations

import json
import os
import re
import sys
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any

import requests
from mcp.server.fastmcp import FastMCP


BASE_URL = os.environ.get("WOLAI_BASE_URL", "https://openapi.wolai.com/v1")
SERVER_NAME = os.environ.get("WOLAI_MCP_SERVER_NAME", "wolai-knowledge-base-plus")
MAX_READ_PAGE_SIZE = 200
DEFAULT_DATABASE_PAGE_SIZE = 100
MAX_CREATE_BATCH_SIZE = 20
DEFAULT_MAX_QPS = 5
CREATE_BLOCK_TYPE_ALIASES = {
    "bulleted_list": "bull_list",
    "numbered_list": "enum_list",
    "equation": "block_equation",
}

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
    children_cache: dict[tuple[str, int, str], "PaginatedResult"] = field(default_factory=dict)
    database_cache: dict[tuple[str, int, str], "PaginatedResult"] = field(default_factory=dict)


@dataclass
class PaginatedResult:
    items: list[dict[str, Any]]
    has_more: bool = False
    next_cursor: str = ""
    total_count: int | None = None


class WolaiClient:
    def __init__(self) -> None:
        self._token: str | None = None
        self._request_times: deque[float] = deque()
        self._rate_lock = threading.Lock()
        try:
            self._max_qps = int(os.environ.get("WOLAI_MAX_QPS", str(DEFAULT_MAX_QPS)))
        except ValueError:
            self._max_qps = DEFAULT_MAX_QPS
        self._max_qps = max(1, self._max_qps)

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
        self._wait_for_rate_limit()
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

    def _wait_for_rate_limit(self) -> None:
        while True:
            with self._rate_lock:
                now = time.monotonic()
                while self._request_times and now - self._request_times[0] >= 1.0:
                    self._request_times.popleft()
                if len(self._request_times) < self._max_qps:
                    self._request_times.append(now)
                    return
                sleep_seconds = 1.0 - (now - self._request_times[0])
            time.sleep(max(0.01, sleep_seconds))

    def request_json(
        self,
        method: str,
        path: str,
        *,
        budget: RequestBudget | None = None,
        json_body: Any | None = None,
        params: dict[str, Any] | None = None,
        retry: int = 3,
    ) -> dict[str, Any]:
        if budget is not None:
            budget.take()
        url = f"{BASE_URL}{path}"
        clean_params = {k: v for k, v in (params or {}).items() if v not in ("", None)}
        last_error: Exception | None = None
        for attempt in range(retry + 1):
            try:
                headers = self.headers()
                self._wait_for_rate_limit()
                response = requests.request(
                    method,
                    url,
                    headers=headers,
                    json=json_body,
                    params=clean_params or None,
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
                if not response.ok:
                    raise WolaiError(f"{response.status_code} {response.text[:500]}")
                return response.json()
            except Exception as exc:  # noqa: BLE001 - return readable MCP errors.
                last_error = exc
                if attempt < retry:
                    time.sleep(min(1.0 * (attempt + 1), 3.0))
                    continue
                break
        query = f" params={clean_params}" if clean_params else ""
        raise WolaiError(f"{method} {path}{query} failed: {last_error}") from last_error

    def _page_params(self, page_size: int, cursor: str = "") -> dict[str, Any]:
        return {"page_size": normalize_page_size(page_size), "start_cursor": cursor}

    def get_block(self, block_id: str, state: RenderState | None = None) -> dict[str, Any]:
        if state and block_id in state.block_cache:
            return state.block_cache[block_id]
        data = self.request_json("GET", f"/blocks/{block_id}", budget=state.budget if state else None)
        block = data.get("data", {})
        if state is not None:
            state.block_cache[block_id] = block
        return block

    def get_children_page(
        self,
        block_id: str,
        state: RenderState | None = None,
        *,
        page_size: int = MAX_READ_PAGE_SIZE,
        cursor: str = "",
    ) -> PaginatedResult:
        page_size = normalize_page_size(page_size)
        cache_key = (block_id, page_size, cursor)
        if state and cache_key in state.children_cache:
            return state.children_cache[cache_key]
        local_offset = parse_offset_cursor(cursor)
        data = self.request_json(
            "GET",
            f"/blocks/{block_id}/children",
            budget=state.budget if state else None,
            params=self._page_params(page_size, "" if local_offset is not None else cursor),
        )
        result = parse_paginated_result(data, "children")
        if (result.has_more and not result.next_cursor) or local_offset is not None:
            result = self._children_from_parent_ids(
                block_id,
                state,
                page_size=page_size,
                local_offset=local_offset or 0,
                fallback=result,
            )
        else:
            result = apply_local_pagination(
                result,
                page_size=page_size,
                local_offset=local_offset,
            )
        if state is not None:
            state.children_cache[cache_key] = result
            for child in result.items:
                child_id = child.get("id")
                if child_id:
                    state.block_cache[child_id] = child
        return result

    def get_children(self, block_id: str, state: RenderState | None = None) -> list[dict[str, Any]]:
        return self.get_children_page(block_id, state, page_size=MAX_READ_PAGE_SIZE).items

    def _children_from_parent_ids(
        self,
        block_id: str,
        state: RenderState | None,
        *,
        page_size: int,
        local_offset: int,
        fallback: PaginatedResult,
    ) -> PaginatedResult:
        try:
            parent = self.get_block(block_id, state)
        except Exception:  # noqa: BLE001 - keep the direct children result if fallback lookup fails.
            return fallback
        ids = parent.get("children", {}).get("ids", [])
        if not isinstance(ids, list) or not ids:
            return fallback

        offset = max(0, local_offset)
        page_ids = [str(item) for item in ids[offset : offset + page_size] if item]
        items = [self.get_block(child_id, state) for child_id in page_ids]
        next_offset = offset + page_size
        has_more = next_offset < len(ids)
        return PaginatedResult(
            items=items,
            has_more=has_more,
            next_cursor=f"offset:{next_offset}" if has_more else "",
            total_count=len(ids),
        )

    def get_database_page(
        self,
        database_id: str,
        state: RenderState | None = None,
        *,
        page_size: int = DEFAULT_DATABASE_PAGE_SIZE,
        cursor: str = "",
    ) -> PaginatedResult:
        page_size = normalize_page_size(page_size)
        cache_key = (database_id, page_size, cursor)
        if state and cache_key in state.database_cache:
            return state.database_cache[cache_key]
        local_offset = parse_offset_cursor(cursor)
        data = self.request_json(
            "GET",
            f"/databases/{database_id}",
            budget=state.budget if state else None,
            params=self._page_params(page_size, "" if local_offset is not None else cursor),
        )
        result = apply_local_pagination(
            parse_paginated_result(data, "rows"),
            page_size=page_size,
            local_offset=local_offset,
        )
        if state is not None:
            state.database_cache[cache_key] = result
        return result

    def get_database(self, database_id: str, state: RenderState | None = None) -> dict[str, Any]:
        page = self.get_database_page(database_id, state, page_size=MAX_READ_PAGE_SIZE)
        return {
            "rows": page.items,
            "has_more": page.has_more,
            "next_cursor": page.next_cursor,
            "total_count": page.total_count,
        }

    def create_blocks(self, parent_id: str, blocks: list[dict[str, Any]]) -> dict[str, Any]:
        chunks = []
        normalized_blocks = [normalize_create_block(block) for block in blocks]
        for index, chunk in enumerate(chunked(normalized_blocks, MAX_CREATE_BATCH_SIZE), 1):
            response = self.request_json(
                "POST",
                "/blocks",
                json_body={"parent_id": parent_id, "blocks": chunk},
            )
            chunks.append({"index": index, "submitted_count": len(chunk), "response": response})
        return chunked_write_result("blocks", len(blocks), chunks)

    def create_database_rows(self, database_id: str, rows: list[dict[str, Any]]) -> dict[str, Any]:
        chunks = []
        for index, chunk in enumerate(chunked(rows, MAX_CREATE_BATCH_SIZE), 1):
            response = self.request_json(
                "POST",
                f"/databases/{database_id}/rows",
                json_body={"rows": chunk},
            )
            chunks.append({"index": index, "submitted_count": len(chunk), "response": response})
        return chunked_write_result(
            "database_rows",
            len(rows),
            chunks,
            note=(
                "Wolai public OpenAPI currently documents database row creation as row page "
                "creation only. Cell-value writes are not documented and were observed to be "
                "ignored by the API; use the returned row page IDs for page-content writes."
            ),
        )


client = WolaiClient()
mcp = FastMCP(SERVER_NAME)


def json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2)


def normalize_page_size(page_size: int, *, default: int = MAX_READ_PAGE_SIZE) -> int:
    try:
        size = int(page_size)
    except (TypeError, ValueError):
        size = default
    return min(max(1, size), MAX_READ_PAGE_SIZE)


def parse_total_count(payload: dict[str, Any]) -> int | None:
    for key in ("total_count", "total", "count"):
        value = payload.get(key)
        if isinstance(value, bool):
            continue
        if isinstance(value, int):
            return value
        if isinstance(value, str) and value.isdigit():
            return int(value)
    return None


def parse_paginated_result(response: dict[str, Any], item_key: str) -> PaginatedResult:
    payload = response.get("data", {})
    items: list[dict[str, Any]] = []
    has_more = bool(response.get("has_more", False))
    next_cursor = str(response.get("next_cursor") or "")
    total_count = parse_total_count(response)

    if isinstance(payload, list):
        items = [item for item in payload if isinstance(item, dict)]
    elif isinstance(payload, dict):
        for key in (item_key, "children", "rows", "blocks", "items", "records"):
            candidate = payload.get(key)
            if isinstance(candidate, list):
                items = [item for item in candidate if isinstance(item, dict)]
                break
        has_more = bool(payload.get("has_more", has_more))
        next_cursor = str(payload.get("next_cursor") or next_cursor)
        total_count = parse_total_count(payload) if total_count is None else total_count

    return PaginatedResult(
        items=items,
        has_more=has_more,
        next_cursor=next_cursor,
        total_count=total_count,
    )


def parse_offset_cursor(cursor: str) -> int | None:
    if not cursor:
        return None
    value = cursor.removeprefix("offset:")
    if value.isdigit():
        return int(value)
    return None


def apply_local_pagination(
    result: PaginatedResult,
    *,
    page_size: int,
    local_offset: int | None,
) -> PaginatedResult:
    if local_offset is None and len(result.items) <= page_size:
        return result

    offset = local_offset or 0
    items = result.items[offset : offset + page_size]
    next_offset = offset + page_size
    has_more_local = next_offset < len(result.items)
    next_cursor = f"offset:{next_offset}" if has_more_local else result.next_cursor
    has_more = has_more_local or result.has_more
    total_count = result.total_count if result.total_count is not None else len(result.items)
    return PaginatedResult(
        items=items,
        has_more=has_more,
        next_cursor=next_cursor if has_more else "",
        total_count=total_count,
    )


def chunked(items: list[dict[str, Any]], size: int) -> list[list[dict[str, Any]]]:
    return [items[index : index + size] for index in range(0, len(items), size)]


def chunked_write_result(
    resource: str,
    submitted_count: int,
    chunks: list[dict[str, Any]],
    *,
    note: str = "",
) -> dict[str, Any]:
    created_ids = created_ids_from_chunks(chunks)
    result: dict[str, Any] = {
        "resource": resource,
        "submitted_count": submitted_count,
        "wolai_create_limit_per_request": MAX_CREATE_BATCH_SIZE,
        "chunk_count": len(chunks),
        "created_ids": created_ids,
        "chunks": chunks,
    }
    if note:
        result["note"] = note
    if len(chunks) == 1:
        result["data"] = chunks[0].get("response", {}).get("data")
    return result


def created_ids_from_chunks(chunks: list[dict[str, Any]]) -> list[str]:
    ids: list[str] = []
    seen: set[str] = set()
    for chunk in chunks:
        collect_created_ids(chunk.get("response"), ids, seen)
    return ids


def collect_created_ids(value: Any, ids: list[str], seen: set[str]) -> None:
    if isinstance(value, dict):
        if isinstance(value.get("id"), str):
            add_created_id(value["id"], ids, seen)
        for child in value.values():
            collect_created_ids(child, ids, seen)
        return
    if isinstance(value, list):
        for child in value:
            collect_created_ids(child, ids, seen)
        return
    if isinstance(value, str):
        match = re.search(r"#([A-Za-z0-9]+)", value) or re.search(r"wolai\.com/([A-Za-z0-9]+)", value)
        if match:
            add_created_id(match.group(1), ids, seen)


def add_created_id(block_id: str, ids: list[str], seen: set[str]) -> None:
    if block_id not in seen:
        ids.append(block_id)
        seen.add(block_id)


def pagination_summary(
    *,
    returned_count: int,
    page_size: int,
    has_more: bool,
    next_cursor: str,
    total_count: int | None,
) -> list[str]:
    return [
        f"returned_count: {returned_count}",
        f"page_size: {page_size} (max {MAX_READ_PAGE_SIZE})",
        f"has_more: {str(has_more).lower()}",
        f"next_cursor: {next_cursor or '(none)'}",
        f"total_count: {total_count if total_count is not None else 'unknown'}",
        f"is_complete: {str(not has_more).lower()}",
    ]


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
        annotations = item.get("annotations") if isinstance(item.get("annotations"), dict) else {}
        if item.get("bold") or annotations.get("bold"):
            title = f"**{title}**"
        if item.get("italic") or annotations.get("italic"):
            title = f"*{title}*"
        if item.get("strikethrough") or annotations.get("strikethrough"):
            title = f"~~{title}~~"
        if item.get("underline") or annotations.get("underline"):
            title = f"<u>{title}</u>"
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
    if block_type in {"bull_list", "bulleted_list"}:
        return f"{indent}- {text}{suffix}"
    if block_type in {"enum_list", "numbered_list"}:
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
    page = client.get_database_page(database_id, state, page_size=MAX_READ_PAGE_SIZE)
    rows = page.items
    if not rows:
        lines = [f"{indent}[database rows] No rows returned on this page."]
    else:
        lines = [
            f"{indent}[database rows] {len(rows)} row(s) returned "
            f"(page_size={MAX_READ_PAGE_SIZE}, has_more={str(page.has_more).lower()})"
        ]
    if page.total_count is not None:
        lines.append(f"{indent}[database total_count] {page.total_count}")
    if page.has_more:
        lines.append(
            f"{indent}[database has more rows] next_cursor={page.next_cursor}; "
            "call get_database_rows with cursor to continue."
        )
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
        child_page = client.get_children_page(block_id, state, page_size=MAX_READ_PAGE_SIZE)
        for child in child_page.items:
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
        if child_page.has_more:
            lines.append(
                f"{indent}  [children has_more=true next_cursor={child_page.next_cursor}; "
                "call list_child_blocks with cursor to continue]"
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


def normalize_create_block(block: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(block)
    block_type = normalized.get("type")
    if isinstance(block_type, str):
        normalized["type"] = CREATE_BLOCK_TYPE_ALIASES.get(block_type, block_type)
    return normalized


def simple_block(content: str, block_type: str = "text", *, language: str = "") -> dict[str, Any]:
    block_type = CREATE_BLOCK_TYPE_ALIASES.get(block_type, block_type)
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
        "block/page creation, code blocks, and database row-page creation.\n"
        "Pagination: resource list tools expose page_size and cursor. Wolai reads "
        "at most 200 records per request and returns has_more/next_cursor when more "
        "data is available. Agents should keep calling with cursor when they need "
        "the next page.\n"
        "Rate/write limits: Wolai documents a same-user limit of 5 requests/second "
        "and a create limit of 20 records/request. This server throttles requests "
        "inside the process and chunks create calls into batches of 20 while still "
        "reporting the chunks in the response.\n"
        "Known Wolai OpenAPI surface: public docs expose Token, Block "
        "create/detail/children, Database get/create rows, and Token refresh. Block "
        "creation does not accept database blocks, so this server cannot create a "
        "new database from OpenAPI alone. Database row creation creates row pages; "
        "cell-value writes are not documented and were observed to be ignored. "
        "Block update/delete and database row update/delete are not clearly "
        "advertised, so this server does not pretend they are safely supported."
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
- list_child_blocks(block_id="", page_size=200, cursor="")
  List one page of immediate child blocks. Defaults to WOLAI_ROOT_ID.
  Wolai max page_size is 200. If has_more=true, call again with next_cursor.

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
                    page_depth=1, page_size=100, cursor="",
                    max_output_chars=30000)
  Read one page of database rows and row page IDs. Can optionally render each
  row page. Wolai max page_size is 200. If has_more=true, call again with
  next_cursor.
- search_tree(query, start_id="", max_depth=3, include_blocks=True,
              include_databases=True, request_budget=250)
  Traverse from a root and search page/block titles plus database row titles.

Write:
- create_page(title, parent_id="")
  Create a child page under parent_id, or under WOLAI_ROOT_ID if omitted.
- add_text_blocks(parent_id, content, block_type="text")
  Append plain text lines as simple blocks.
  Creation aliases are accepted: bulleted_list->bull_list,
  numbered_list->enum_list, equation->block_equation.
- add_code_block(parent_id, code, language="python")
  Append one code block.
- add_blocks(parent_id, blocks_json)
  Append raw Wolai block JSON for advanced cases.
  Creation aliases are accepted for common agent names: bulleted_list,
  numbered_list, and equation.
- create_database_rows(database_id, rows_json)
  Create database row pages with raw Wolai row JSON. Wolai public docs show
  empty row objects; cell-value writes are not documented and may be ignored.

Safety notes:
- The server never prints WOLAI_APP_SECRET.
- Wolai limits are visible to agents: reads are paged to max 200 records,
  creates are chunked to max 20 records/request, and requests are throttled to
  5/second inside this MCP process.
- read_block has request_budget and 429 retry/backoff.
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
def list_child_blocks(block_id: str = "", page_size: int = MAX_READ_PAGE_SIZE, cursor: str = "") -> str:
    """List one page of immediate child blocks for a page/block."""
    block_id = block_id or client.root_id()
    if not block_id:
        return "No block_id provided and WOLAI_ROOT_ID is not set."
    try:
        effective_page_size = normalize_page_size(page_size)
        page = client.get_children_page(block_id, page_size=effective_page_size, cursor=cursor)
    except Exception as exc:  # noqa: BLE001
        return f"Error listing children for {block_id}: {exc}"
    lines = [f"Children for {block_id}"]
    lines.extend(
        pagination_summary(
            returned_count=len(page.items),
            page_size=effective_page_size,
            has_more=page.has_more,
            next_cursor=page.next_cursor,
            total_count=page.total_count,
        )
    )
    if page_size != effective_page_size:
        lines.append(f"requested_page_size: {page_size} (clamped to {effective_page_size})")
    if page.has_more:
        lines.append("next_call: list_child_blocks(block_id, page_size, cursor=next_cursor)")
    if not page.items:
        lines.append("No children found on this page.")
        return "\n".join(lines)
    for child in page.items:
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
    page_size: int = DEFAULT_DATABASE_PAGE_SIZE,
    cursor: str = "",
    max_output_chars: int = 30000,
) -> str:
    """Read one page of Wolai database rows, including row page IDs."""
    try:
        effective_page_size = normalize_page_size(page_size, default=DEFAULT_DATABASE_PAGE_SIZE)
        policy = RenderPolicy(child_depth=page_depth, max_output_chars=max_output_chars)
        state = RenderState(budget=RequestBudget(200))
        page = client.get_database_page(
            database_id,
            state,
            page_size=effective_page_size,
            cursor=cursor,
        )
        rows = page.items
        lines = [f"Database {database_id}"]
        lines.extend(
            pagination_summary(
                returned_count=len(rows),
                page_size=effective_page_size,
                has_more=page.has_more,
                next_cursor=page.next_cursor,
                total_count=page.total_count,
            )
        )
        if page_size != effective_page_size:
            lines.append(f"requested_page_size: {page_size} (clamped to {effective_page_size})")
        if page.has_more:
            lines.append("next_call: get_database_rows(database_id, page_size, cursor=next_cursor)")
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
    notes: list[str] = []
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
                db_page = client.get_database_page(current_id, state, page_size=MAX_READ_PAGE_SIZE)
                for row in db_page.items:
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
                if db_page.has_more:
                    notes.append(
                        f"- [database partial] {current_id} returned {len(db_page.items)} rows; "
                        f"next_cursor={db_page.next_cursor}"
                    )
            if depth < max_depth:
                child_page = client.get_children_page(current_id, state, page_size=MAX_READ_PAGE_SIZE)
                for child in child_page.items:
                    child_id = child.get("id")
                    child_type = str(child.get("type", ""))
                    if child_id and (include_blocks or child_type in {"page", "database"}):
                        queue.append((str(child_id), depth + 1))
                if child_page.has_more:
                    notes.append(
                        f"- [children partial] {current_id} returned {len(child_page.items)} children; "
                        f"next_cursor={child_page.next_cursor}"
                    )
        if not results:
            text = f"No matches for '{query}' within depth {max_depth} of {start_id}."
            if notes:
                text += "\n\nPartial-scan notes:\n" + "\n".join(notes)
            return text
        if notes:
            results.extend(["", "Partial-scan notes:", *notes])
        return "\n".join(results)
    except Exception as exc:  # noqa: BLE001
        partial = "\n".join(results)
        if partial and notes:
            partial += "\n\nPartial-scan notes:\n" + "\n".join(notes)
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
    Create database row pages using raw Wolai row JSON.

    Wolai public docs show empty row objects. Cell-value writes are not
    documented and may be ignored by the API.
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
