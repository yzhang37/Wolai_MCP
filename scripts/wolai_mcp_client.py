from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import tomllib
from pathlib import Path
from typing import Any

import requests
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


DEFAULT_CODEX_CONFIG = Path(r"C:\Users\lacus\.codex\config.toml")
DEFAULT_SERVER_NAME = "wolai-kb"
BASE_URL = "https://openapi.wolai.com/v1"
INLINE_EXPANDABLE_TYPES = {
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


def configure_output() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except AttributeError:
        pass


def load_server_config(config_path: Path, server_name: str) -> dict[str, Any]:
    data = tomllib.loads(config_path.read_text(encoding="utf-8"))
    try:
        return data["mcp_servers"][server_name]
    except KeyError as exc:
        raise SystemExit(
            f"Could not find [mcp_servers.{server_name}] in {config_path}"
        ) from exc


def result_text(result: Any) -> str:
    return "\n".join(getattr(item, "text", str(item)) for item in result.content)


def get_wolai_token(server: dict[str, Any]) -> str:
    env = server.get("env", {})
    response = requests.post(
        f"{BASE_URL}/token",
        json={
            "appId": env["WOLAI_APP_ID"],
            "appSecret": env["WOLAI_APP_SECRET"],
        },
        timeout=20,
    )
    response.raise_for_status()
    return response.json()["data"]["app_token"]


def wolai_headers(token: str) -> dict[str, str]:
    return {"Authorization": token, "Content-Type": "application/json"}


def get_wolai_block(token: str, block_id: str) -> dict[str, Any]:
    response = requests.get(
        f"{BASE_URL}/blocks/{block_id}",
        headers=wolai_headers(token),
        timeout=20,
    )
    response.raise_for_status()
    return response.json().get("data", {})


def get_wolai_children(token: str, block_id: str) -> list[dict[str, Any]]:
    response = requests.get(
        f"{BASE_URL}/blocks/{block_id}/children",
        headers=wolai_headers(token),
        timeout=20,
    )
    response.raise_for_status()
    return response.json().get("data", [])


def parse_rich_text(content: Any) -> tuple[str, list[tuple[str, str]]]:
    if not isinstance(content, list):
        return str(content or ""), []

    text_parts = []
    inline_links = []
    for item in content:
        if not isinstance(item, dict):
            text_parts.append(str(item))
            continue

        title = item.get("title", "")
        if item.get("bold"):
            title = f"**{title}**"
        if item.get("italic"):
            title = f"*{title}*"
        text_parts.append(title)

        if item.get("type") == "bi_link" and item.get("block_id"):
            inline_links.append((item.get("title", "linked block"), item["block_id"]))

    return "".join(text_parts), inline_links


def format_block_line(block_type: str, text: str, block_id: str, depth: int) -> str:
    indent = "  " * depth
    if block_type == "divider":
        return f"{indent}---"
    if block_type == "page":
        return f"{indent}# {text} (ID: {block_id})"
    if block_type == "heading":
        return f"{indent}## {text}"
    if block_type in {"enum_list", "bulleted_list"}:
        return f"{indent}- {text}"
    if block_type in {"bull_list", "numbered_list"}:
        return f"{indent}1. {text}"
    if block_type == "todo_list":
        return f"{indent}- [ ] {text}"
    if block_type == "toggle_list":
        return f"{indent}> {text}"
    return f"{indent}[{block_type}] {text}" if block_type else f"{indent}{text}"


def render_expanded_block(
    token: str,
    block_id: str,
    *,
    depth: int = 0,
    max_depth: int = 4,
    visited: set[str] | None = None,
    cache: dict[str, dict[str, Any]] | None = None,
    expand_inline_links: bool = True,
) -> list[str]:
    if visited is None:
        visited = set()
    if cache is None:
        cache = {}
    if block_id in visited:
        return [f"{'  ' * depth}[cycle skipped: {block_id}]"]
    if depth > max_depth:
        return [f"{'  ' * depth}[max depth reached: {block_id}]"]

    visited.add(block_id)
    block = cache.get(block_id)
    if block is None:
        block = get_wolai_block(token, block_id)
        cache[block_id] = block
    block_type = block.get("type", "")

    if block_type == "reference":
        source_id = block.get("source_block_id")
        if not source_id:
            return [f"{'  ' * depth}[reference] (unresolved, ID: {block_id})"]
        lines = [f"{'  ' * depth}[reference -> {source_id}]"]
        lines.extend(
            render_expanded_block(
                token,
                source_id,
                depth=depth + 1,
                max_depth=max_depth,
                visited=visited,
                cache=cache,
                expand_inline_links=expand_inline_links,
            )
        )
        return lines

    text, inline_links = parse_rich_text(block.get("content", ""))
    lines = [format_block_line(block_type, text, block_id, depth)]

    if expand_inline_links:
        for label, linked_id in inline_links:
            lines.append(f"{'  ' * (depth + 1)}[inline link: {label} -> {linked_id}]")
            linked_block = cache.get(linked_id)
            if linked_block is None:
                linked_block = get_wolai_block(token, linked_id)
                cache[linked_id] = linked_block
            linked_type = linked_block.get("type", "")
            if linked_type not in INLINE_EXPANDABLE_TYPES:
                lines.append(
                    f"{'  ' * (depth + 2)}"
                    f"[inline target not expanded: type={linked_type or 'unknown'}]"
                )
                continue
            lines.extend(
                render_expanded_block(
                    token,
                    linked_id,
                    depth=depth + 2,
                    max_depth=max_depth,
                    visited=visited,
                    cache=cache,
                    expand_inline_links=expand_inline_links,
                )
            )

    for child in get_wolai_children(token, block_id):
        child_id = child.get("id")
        if not child_id:
            continue
        lines.extend(
            render_expanded_block(
                token,
                child_id,
                depth=depth + 1,
                max_depth=max_depth,
                visited=visited,
                cache=cache,
                expand_inline_links=expand_inline_links,
            )
        )

    return lines


async def with_session(args: argparse.Namespace, callback):
    server = load_server_config(args.config, args.server)
    env = os.environ.copy()
    env.update(server.get("env", {}))

    params = StdioServerParameters(
        command=server["command"],
        args=server.get("args", []),
        env=env,
    )

    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            return await callback(session, server)


async def cmd_tools(args: argparse.Namespace) -> None:
    async def run(session: ClientSession, _server: dict[str, Any]) -> None:
        tools = await session.list_tools()
        for tool in tools.tools:
            print(tool.name)

    await with_session(args, run)


async def cmd_root(args: argparse.Namespace) -> None:
    async def run(session: ClientSession, _server: dict[str, Any]) -> None:
        result = await session.call_tool("get_root_info", {})
        print(result_text(result))

    await with_session(args, run)


async def cmd_children(args: argparse.Namespace) -> None:
    async def run(session: ClientSession, server: dict[str, Any]) -> None:
        block_id = args.block_id or server.get("env", {}).get("WOLAI_ROOT_ID", "")
        if not block_id:
            raise SystemExit("No block_id provided and WOLAI_ROOT_ID is not set.")
        result = await session.call_tool("list_child_blocks", {"block_id": block_id})
        print(result_text(result))

    await with_session(args, run)


async def cmd_page(args: argparse.Namespace) -> None:
    async def run(session: ClientSession, server: dict[str, Any]) -> None:
        block_id = args.block_id or server.get("env", {}).get("WOLAI_ROOT_ID", "")
        if not block_id:
            raise SystemExit("No block_id provided and WOLAI_ROOT_ID is not set.")
        result = await session.call_tool("get_page_content", {"block_id": block_id})
        text = result_text(result)
        if args.limit and len(text) > args.limit:
            text = text[: args.limit] + "\n\n...[truncated]"
        print(text)

    await with_session(args, run)


async def cmd_search(args: argparse.Namespace) -> None:
    async def run(session: ClientSession, _server: dict[str, Any]) -> None:
        payload = {"query": args.query, "max_depth": args.max_depth}
        if args.start_id:
            payload["start_id"] = args.start_id
        result = await session.call_tool("search_pages_by_title", payload)
        print(result_text(result))

    await with_session(args, run)


async def cmd_database(args: argparse.Namespace) -> None:
    server = load_server_config(args.config, args.server)
    token = get_wolai_token(server)
    response = requests.get(
        f"{BASE_URL}/databases/{args.database_id}",
        headers=wolai_headers(token),
        timeout=30,
    )
    response.raise_for_status()
    payload = response.json()

    rows = payload.get("data", {}).get("rows", [])
    if args.raw:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return

    if not rows:
        print("No database rows found.")
        return

    for row in rows:
        page_id = row.get("page_id", "")
        data = row.get("data", {})
        title = ""
        fields = []
        for key, cell in data.items():
            value = cell.get("value", "") if isinstance(cell, dict) else cell
            if not value:
                continue
            if cell.get("type") == "primary":
                title = str(value)
            else:
                fields.append(f"{key}={value}")
        suffix = f" | {'; '.join(fields)}" if fields else ""
        print(f"- {title or '(Untitled)'} (page_id: {page_id}){suffix}")


async def cmd_page_expanded(args: argparse.Namespace) -> None:
    server = load_server_config(args.config, args.server)
    block_id = args.block_id or server.get("env", {}).get("WOLAI_ROOT_ID", "")
    if not block_id:
        raise SystemExit("No block_id provided and WOLAI_ROOT_ID is not set.")

    token = get_wolai_token(server)
    lines = render_expanded_block(
        token,
        block_id,
        max_depth=args.max_depth,
        expand_inline_links=not args.no_inline_links,
    )
    text = "\n".join(lines)
    if args.limit and len(text) > args.limit:
        text = text[: args.limit] + "\n\n...[truncated]"
    print(text)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Call the local Wolai MCP server.")
    parser.add_argument("--config", type=Path, default=DEFAULT_CODEX_CONFIG)
    parser.add_argument("--server", default=DEFAULT_SERVER_NAME)

    sub = parser.add_subparsers(dest="command", required=True)

    tools = sub.add_parser("tools", help="List MCP tools.")
    tools.set_defaults(func=cmd_tools)

    root = sub.add_parser("root", help="Read configured root info.")
    root.set_defaults(func=cmd_root)

    children = sub.add_parser("children", help="List child blocks.")
    children.add_argument("block_id", nargs="?")
    children.set_defaults(func=cmd_children)

    page = sub.add_parser("page", help="Read page content.")
    page.add_argument("block_id", nargs="?")
    page.add_argument("--limit", type=int, default=0)
    page.set_defaults(func=cmd_page)

    page_expanded = sub.add_parser(
        "page-expanded",
        help="Read page content and recursively expand reference blocks.",
    )
    page_expanded.add_argument("block_id", nargs="?")
    page_expanded.add_argument("--max-depth", type=int, default=4)
    page_expanded.add_argument("--limit", type=int, default=0)
    page_expanded.add_argument(
        "--no-inline-links",
        action="store_true",
        help="Do not expand inline bi_link references.",
    )
    page_expanded.set_defaults(func=cmd_page_expanded)

    search = sub.add_parser("search", help="Search page titles.")
    search.add_argument("query")
    search.add_argument("--start-id", default="")
    search.add_argument("--max-depth", type=int, default=2)
    search.set_defaults(func=cmd_search)

    database = sub.add_parser("database", help="Read Wolai database rows.")
    database.add_argument("database_id")
    database.add_argument("--raw", action="store_true", help="Print raw JSON.")
    database.set_defaults(func=cmd_database)

    return parser


def main() -> None:
    configure_output()
    args = build_parser().parse_args()
    asyncio.run(args.func(args))


if __name__ == "__main__":
    main()
