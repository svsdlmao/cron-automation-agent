import os
from pathlib import Path

import pytest

from cron_agent.crontab import FileCrontab
from cron_agent.jobs import JobManager


@pytest.fixture
def agent_env(tmp_path, monkeypatch):
    """Point the agent at a throwaway home with a file crontab and a scripts folder."""
    home = tmp_path / "agent"
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    for name in ("db_backup.sh", "cleanup_tmp.sh", "weekly_report.py"):
        (scripts / name).write_text("#!/bin/sh\necho ok\n")
    monkeypatch.setenv("CRON_AGENT_HOME", str(home))
    monkeypatch.setenv("CRON_AGENT_BACKEND", "file")
    monkeypatch.setenv("CRON_AGENT_PROVIDER", "rules")
    monkeypatch.setenv("CRON_AGENT_SCRIPT_DIRS", str(scripts))
    return {"home": home, "scripts": scripts, "crontab": home / "crontab.txt"}


@pytest.fixture
def manager(agent_env) -> JobManager:
    return JobManager(FileCrontab(agent_env["crontab"]), agent_env["home"])


USER_LINES = "# my own jobs\nMAILTO=me@example.com\n15 3 * * * /usr/local/bin/renew-certs\n"


@pytest.fixture
def user_crontab(agent_env) -> Path:
    path = agent_env["crontab"]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(USER_LINES)
    return path
