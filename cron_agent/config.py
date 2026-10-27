"""Runtime settings, all overridable through environment variables."""

from __future__ import annotations

import os
import shutil
from pathlib import Path


def agent_home() -> Path:
    """Folder for backups, logs and the lock file (default ~/.cron-agent)."""
    return Path(os.environ.get("CRON_AGENT_HOME", Path.home() / ".cron-agent")).expanduser()


def backend_name() -> str:
    """'system' uses the real crontab binary; 'file' uses a plain file (tests, Windows demo)."""
    name = os.environ.get("CRON_AGENT_BACKEND", "auto").lower()
    if name == "auto":
        return "system" if shutil.which("crontab") else "file"
    if name not in ("system", "file"):
        raise ValueError(f"CRON_AGENT_BACKEND must be system, file or auto, not {name!r}")
    return name


def provider_name() -> str:
    """Which planner turns English into a tool call: hybrid (default), rules, ollama, anthropic, openai."""
    return os.environ.get("CRON_AGENT_PROVIDER", "hybrid").lower()


def model_name(default: str) -> str:
    return os.environ.get("CRON_AGENT_MODEL", default)


def script_dirs() -> list[Path]:
    """Folders searched by find_script when the user names a script instead of a path."""
    raw = os.environ.get("CRON_AGENT_SCRIPT_DIRS")
    if raw:
        return [Path(p).expanduser() for p in raw.split(os.pathsep) if p]
    home = Path.home()
    return [home / "scripts", home / "bin", home / ".local" / "bin"]
