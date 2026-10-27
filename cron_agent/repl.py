"""The interface layer: a custom REPL with history (prompt_toolkit), or plain stdin when piped."""

from __future__ import annotations

import asyncio
import sys

from . import __version__, config
from .agent import Agent
from .client import ToolClient
from .planner import HybridPlanner, LLMPlanner, RulesPlanner
from .providers import ProviderError

BANNER = """cron-agent {version} · planner: {planner} · backend: {backend}
Describe a job in plain English, e.g. “run ~/scripts/db_backup.sh every Friday at midnight”.
Type help for examples, exit to quit."""


def _make_ask():
    interactive = sys.stdin.isatty() and sys.stdout.isatty()
    if interactive:
        from prompt_toolkit import PromptSession
        from prompt_toolkit.history import FileHistory

        home = config.agent_home()
        home.mkdir(parents=True, exist_ok=True)
        session = PromptSession(history=FileHistory(str(home / "history")))

        async def ask(prompt: str) -> str:
            return await session.prompt_async(prompt)
    else:
        async def ask(prompt: str) -> str:
            print(prompt, end="", flush=True)
            line = await asyncio.to_thread(sys.stdin.readline)
            if not line:
                raise EOFError
            line = line.rstrip("\r\n").lstrip("﻿")  # PowerShell pipes add a BOM
            print(line)  # echo, so piped sessions read like a transcript
            return line
    return ask


async def run() -> None:
    ask = _make_ask()
    async with ToolClient.start() as tools:
        name = config.provider_name()
        try:
            if name == "rules":
                planner = RulesPlanner()
            elif name == "hybrid":
                planner = HybridPlanner(tools.tools)
            else:
                planner = LLMPlanner(name, tools.tools)
        except ProviderError as exc:
            print(f"{name} unavailable ({exc}); using the offline rules planner.")
            planner = RulesPlanner()
        print(BANNER.format(version=__version__, planner=planner.name, backend=config.backend_name()))
        agent = Agent(tools, planner, ask)
        while True:
            try:
                line = await ask("\ncron-agent> ")
            except (EOFError, KeyboardInterrupt):
                break
            if line.strip().lower() in {"exit", "quit", ":q"}:
                break
            try:
                reply = await agent.handle(line)
            except (EOFError, KeyboardInterrupt):
                break
            if reply:
                print(reply)
    print("bye")
