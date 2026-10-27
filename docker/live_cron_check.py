"""Install a job through the agent's safe path, wait for the real cron daemon to run it, clean up.

The command uses date +%H:%M:%S on purpose: it only works if the agent escaped
every % correctly, because cron treats a bare % as the end of the command.
"""

import subprocess
import sys
import time
from pathlib import Path

from cron_agent.crontab import SystemCrontab
from cron_agent.jobs import JobManager

PROOF = Path("/tmp/cron-agent-proof.txt")
USER_LINE = "17 3 * * * /bin/true  # the user's own job, must survive\n"


def crontab_l() -> str:
    return subprocess.run(["crontab", "-l"], capture_output=True, text=True).stdout


def main() -> int:
    PROOF.unlink(missing_ok=True)
    subprocess.run(["crontab", "-"], input=USER_LINE, text=True, check=True)
    manager = JobManager(SystemCrontab(), Path.home() / ".cron-agent")

    change = manager.plan_add(["* * * * *"], f"echo ran at $(date +%H:%M:%S) >> {PROOF}")
    manager.apply(change, change.base_hash)
    print("crontab -l after install:\n" + crontab_l())

    deadline = time.time() + 130
    while time.time() < deadline and not PROOF.exists():
        time.sleep(2)
    if not PROOF.exists():
        print("FAIL: cron did not run the job within 130 s")
        return 1
    print("proof file written by crond:", PROOF.read_text().strip())
    print("job log:", manager.log_path(change.job_id).exists() and "present" or "missing")

    manager.apply(manager.plan_remove(change.job_id))
    after = crontab_l()
    print("crontab -l after remove:\n" + after)
    if after != USER_LINE:
        print("FAIL: the user's own line was not preserved exactly")
        return 1
    print("PASS: installed, executed by crond, removed; user's line untouched")
    return 0


if __name__ == "__main__":
    sys.exit(main())
