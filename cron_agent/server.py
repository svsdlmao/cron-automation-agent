"""The tooling layer: an MCP server ("cron-tools") and the only part that touches the OS.

Read tools run freely. Write tools take dry_run=True first; the client shows the
preview, asks the user, then calls again with the preview's job_id and base_hash.
"""

from __future__ import annotations

import logging
import platform
import shutil
import subprocess

try:  # MCP Python SDK 2.x renamed FastMCP to MCPServer
    from mcp.server.mcpserver import MCPServer as _MCPServer
    from mcp.server.mcpserver.exceptions import ToolError as _ToolError
except ImportError:  # SDK 1.x
    from mcp.server.fastmcp import FastMCP as _MCPServer
    from mcp.server.fastmcp.exceptions import ToolError as _ToolError

from . import config
from .cronexpr import CronError, CronExpr
from .crontab import CrontabError
from .jobs import Change, JobManager
from .safety import UnsafeCommand
from .scripts import find_scripts

logging.basicConfig(level=logging.WARNING)  # stderr only: stdout carries the MCP protocol
mcp = _MCPServer("cron-tools", log_level="WARNING")


def manager() -> JobManager:
    return JobManager.from_env()


def _schedules(schedule: str) -> list[str]:
    return [s.strip() for s in schedule.split(";") if s.strip()]


def _preview(expr: str, count: int = 5) -> dict:
    parsed = CronExpr.parse(expr)
    return {
        "schedule": str(parsed),
        "description": parsed.describe(),
        "next_runs": [d.strftime("%a %d %b %Y %H:%M") for d in parsed.next_runs(count)],
        "warnings": parsed.warnings(),
    }


def _change(change: Change, extra: dict | None = None) -> dict:
    data = change.public()
    data.update(extra or {})
    return data


def _fail(exc: Exception):
    """A deliberate refusal: the SDK passes its message back to the client as is_error."""
    raise _ToolError(str(exc)) from exc


@mcp.tool()
def list_jobs() -> dict:
    """List the jobs this agent created in the user's crontab (other lines are never touched)."""
    m = manager()
    jobs = [{"id": j.id, "schedules": j.schedules, "command": j.command,
             "description": j.description} for j in m.list_jobs()]
    return {"jobs": jobs, "other_lines": m.other_line_count(), "backend": m.backend.name}


@mcp.tool()
def preview_schedule(schedule: str, count: int = 5) -> dict:
    """Validate a 5-field cron expression and return its English meaning and next run times."""
    try:
        return {"previews": [_preview(s, max(1, min(count, 60))) for s in _schedules(schedule)]}
    except CronError as exc:
        _fail(exc)


@mcp.tool()
def cron_status() -> dict:
    """Report whether the cron daemon is running and how many agent jobs are installed."""
    m = manager()
    if m.backend.name == "file":
        from .daemon import status as daemon_status

        d = daemon_status(m.home)
        daemon = (f"running (cron-agent daemon, pid {d['pid']}, {d['runs']} job runs so far)"
                  if d.get("running") else
                  "not running: start it with `cron-agent --backend file daemon` so jobs run")
    elif shutil.which("pgrep"):
        running = any(subprocess.run(["pgrep", "-x", name], capture_output=True).returncode == 0
                      for name in ("cron", "crond"))
        daemon = "running" if running else "not running"
    else:
        daemon = "unknown (pgrep not available)"
    return {"daemon": daemon, "backend": m.backend.name, "platform": platform.system(),
            "agent_jobs": len(m.list_jobs()), "other_lines": m.other_line_count(),
            "backups": len(m.list_backups())}


@mcp.tool()
def read_job_log(job_id: str, lines: int = 20) -> dict:
    """Return the last lines of a job's output log, to check whether and how it ran."""
    try:
        text = manager().read_log(job_id, max(1, min(lines, 200)))
    except ValueError as exc:
        _fail(exc)
    return {"job_id": job_id, "log": text, "empty": not text}


@mcp.tool()
def find_script(query: str) -> dict:
    """Find scripts in the user's script folders that match a description such as 'database backup'."""
    return {"matches": find_scripts(query, config.script_dirs()),
            "searched": [d.as_posix() for d in config.script_dirs()]}


@mcp.tool()
def add_job(schedule: str, command: str, dry_run: bool = False, job_id: str | None = None,
            base_hash: str | None = None) -> dict:
    """Add a job. schedule is a 5-field cron expression (several separated by ';').

    With dry_run the crontab is not touched and the planned change is returned.
    """
    m = manager()
    try:
        change = m.plan_add(_schedules(schedule), command, job_id)
        previews = [_preview(s, 3) for s in _schedules(schedule)]
        if not dry_run:
            change = m.apply(change, base_hash)
    except (CronError, UnsafeCommand, CrontabError, ValueError) as exc:
        _fail(exc)
    return _change(change, {"previews": previews})


@mcp.tool()
def remove_job(job_id: str, dry_run: bool = False, base_hash: str | None = None) -> dict:
    """Remove one of the agent's jobs by id. Lines the agent did not create are never removed."""
    m = manager()
    try:
        change = m.plan_remove(job_id)
        if not dry_run:
            change = m.apply(change, base_hash)
    except (CrontabError, ValueError) as exc:
        _fail(exc)
    return _change(change)


@mcp.tool()
def rollback(backup_id: str | None = None, dry_run: bool = False,
             base_hash: str | None = None) -> dict:
    """Restore the crontab from a backup (the most recent one by default)."""
    m = manager()
    try:
        change = m.plan_rollback(backup_id)
        if not dry_run:
            change = m.apply(change, base_hash)
    except (CrontabError, ValueError) as exc:
        _fail(exc)
    return _change(change)


def main() -> None:
    mcp.run()  # stdio transport


if __name__ == "__main__":
    main()
