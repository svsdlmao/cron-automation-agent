"""Time-to-execution: from a typed request to the job installed and verified in the crontab.

`python -m cron_agent bench [--provider ollama] [--reps 5]` times the whole loop with an
instant "y" (so human think time is excluded), split into planning and tool time.
It runs against a throwaway file crontab, never your real one.
"""

from __future__ import annotations

import asyncio
import contextlib
import io
import json
import os
import statistics
import tempfile
import time
from pathlib import Path

from . import config
from .agent import Agent
from .client import ToolClient
from .planner import HybridPlanner, LLMPlanner, RulesPlanner

ROOT = Path(__file__).resolve().parents[1]
REQUESTS = [
    "run ~/scripts/db_backup.sh every Friday at midnight",
    "Run my database backup script every Friday at midnight.",
    "every 15 minutes during working hours on weekdays run `echo ok >> /tmp/health.log`",
    "back up my database on the first Monday of every month at 9am",
    "every day at 9:30pm run ~/bin/report.sh",
]


class TimedPlanner:
    def __init__(self, inner):
        self.inner, self.name, self.spent = inner, inner.name, 0.0

    async def plan(self, text, context=""):
        started = time.perf_counter()
        try:
            return await self.inner.plan(text, context)
        finally:
            self.spent += time.perf_counter() - started


def pct(values: list[float], p: float) -> float:
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, max(0, round(p * len(ordered)) - 1))]


async def run_bench(provider: str, reps: int) -> dict:
    home = Path(tempfile.mkdtemp(prefix="cron-agent-bench-"))
    os.environ.update(CRON_AGENT_HOME=str(home), CRON_AGENT_BACKEND="file",
                      CRON_AGENT_SCRIPT_DIRS=str(ROOT / "demo" / "scripts"))
    started = time.perf_counter()
    async with ToolClient.start() as tools:
        startup = time.perf_counter() - started
        inner = (RulesPlanner() if provider == "rules" else HybridPlanner(tools.tools)
                 if provider == "hybrid" else LLMPlanner(provider, tools.tools))
        planner = TimedPlanner(inner)

        async def instant_yes(prompt: str) -> str:
            return "y"

        agent = Agent(tools, planner, instant_yes)
        samples = []
        for _ in range(reps):
            for request in REQUESTS:
                planner.spent = 0.0
                t0 = time.perf_counter()
                with contextlib.redirect_stdout(io.StringIO()):
                    reply = await agent.handle(request)
                total = time.perf_counter() - t0
                samples.append({"request": request, "total_s": total, "planning_s": planner.spent,
                                "tools_s": total - planner.spent,
                                "installed": reply.startswith("Installed")})
    totals = [s["total_s"] for s in samples]
    plans = [s["planning_s"] for s in samples]
    tool_times = [s["tools_s"] for s in samples]
    return {
        "provider": provider,
        "backend": "file",
        "runs": len(samples),
        "installed": sum(s["installed"] for s in samples),
        "server_startup_s": round(startup, 3),
        "total_median_s": round(statistics.median(totals), 4),
        "total_p95_s": round(pct(totals, 0.95), 4),
        "planning_median_s": round(statistics.median(plans), 4),
        "tools_median_s": round(statistics.median(tool_times), 4),
        "samples": samples,
    }


def report_md(r: dict) -> str:
    return "\n".join([
        f"# Time-to-execution: {r['provider']}", "",
        f"{r['runs']} runs ({r['installed']} installed), file backend, instant approval.", "",
        f"- Server start + MCP handshake: {r['server_startup_s']} s (once per session)",
        f"- Request → installed and verified: median {r['total_median_s']} s, "
        f"95th percentile {r['total_p95_s']} s",
        f"- of which planning: median {r['planning_median_s']} s; "
        f"MCP tool calls (lookup, dry run, write, verify): median {r['tools_median_s']} s", ""])


def main(reps: int = 5, out: str | None = None) -> None:
    provider = config.provider_name()
    result = asyncio.run(run_bench(provider, reps))
    out_dir = Path(out) if out else ROOT / "eval" / "results"
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / f"latency-{provider}.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    (out_dir / f"latency-{provider}.md").write_text(report_md(result), encoding="utf-8")
    print(report_md(result))
