"""Against the real crontab binary. Opt-in: CRON_AGENT_SYSTEM_TESTS=1 (it rewrites your crontab)."""

import os
import shutil
import subprocess

import pytest

from cron_agent.crontab import SystemCrontab
from cron_agent.jobs import JobManager

pytestmark = pytest.mark.skipif(
    not shutil.which("crontab") or os.environ.get("CRON_AGENT_SYSTEM_TESTS") != "1",
    reason="needs the crontab binary and CRON_AGENT_SYSTEM_TESTS=1")

USER_LINE = "17 3 * * * /bin/true  # mine\n"


@pytest.fixture
def system_manager(tmp_path):
    saved = subprocess.run(["crontab", "-l"], capture_output=True, text=True)
    subprocess.run(["crontab", "-"], input=USER_LINE, text=True, check=True)
    yield JobManager(SystemCrontab(), tmp_path)
    if saved.returncode == 0:
        subprocess.run(["crontab", "-"], input=saved.stdout, text=True, check=True)
    else:
        subprocess.run(["crontab", "-r"], check=False)


def crontab_l():
    return subprocess.run(["crontab", "-l"], capture_output=True, text=True).stdout


def test_install_through_the_pipe_and_remove(system_manager):
    change = system_manager.apply(system_manager.plan_add(["0 0 * * 5"], "date +%F >> /tmp/x"))
    text = crontab_l()
    assert text.startswith(USER_LINE)
    assert "date +\\%F" in text and f"cron-agent:id={change.job_id}" in text
    system_manager.apply(system_manager.plan_remove(change.job_id))
    assert crontab_l() == USER_LINE


def test_empty_crontab_is_handled(system_manager):
    subprocess.run(["crontab", "-r"], check=True)
    assert system_manager.read() == ""
    change = system_manager.apply(system_manager.plan_add(["@daily"], "echo hi"))
    assert f"cron-agent:id={change.job_id}" in crontab_l()
