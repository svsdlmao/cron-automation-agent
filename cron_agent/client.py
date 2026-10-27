"""MCP client: starts the cron-tools server as a child process and talks JSON-RPC over stdio."""

from __future__ import annotations

import json
import os
import re
import sys
from contextlib import asynccontextmanager
from typing import AsyncIterator

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


class ToolError(RuntimeError):
    """A tool refused or failed; the message is safe to show the user."""


class ToolClient:
    def __init__(self, session: ClientSession, tools):
        self.session = session
        self.tools = tools

    @classmethod
    @asynccontextmanager
    async def start(cls) -> AsyncIterator["ToolClient"]:
        params = StdioServerParameters(command=sys.executable, args=["-m", "cron_agent", "server"],
                                       env=dict(os.environ))
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                tools = (await session.list_tools()).tools
                yield cls(session, tools)

    @property
    def tool_names(self) -> list[str]:
        return [t.name for t in self.tools]

    async def call(self, name: str, args: dict | None = None) -> dict:
        result = await self.session.call_tool(name, args or {})
        text = "\n".join(c.text for c in result.content if getattr(c, "type", "") == "text")
        # field names are snake_case in SDK 2.x and camelCase in 1.x
        is_error = getattr(result, "is_error", getattr(result, "isError", False))
        if is_error:
            raise ToolError(re.sub(r"^Error executing tool \w+: ", "", text).strip())
        structured = getattr(result, "structured_content", getattr(result, "structuredContent", None))
        if isinstance(structured, dict):
            return structured
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return {"text": text}
