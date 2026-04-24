from __future__ import annotations

import argparse
import asyncio
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
    async def run(_session: ClientSession, server: dict[str, Any]) -> None:
        token = get_wolai_token(server)
        response = requests.get(
            f"{BASE_URL}/databases/{args.database_id}",
            headers={"Authorization": token, "Content-Type": "application/json"},
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

    await with_session(args, run)


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
