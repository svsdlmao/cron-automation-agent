"""The built-in cron daemon: parsing like crontab(5), matching, and really running jobs."""

import json
import time
from datetime import datetime, timedelta

from cron_agent.daemon import Daemon, due, parse_crontab, split_percent, status


def test_percent_rules_follow_crontab5():
    assert split_percent("date +\\%F") == ("date +%F", None)
    assert split_percent("mail -s hi%line one%line two") == ("mail -s hi", "line one\nline two\n")


def test_parse_skips_comments_env_and_short_lines():
    text = "# comment\nMAILTO=me@example.com\n\n0 0 * * 5 echo hi # cron-agent:id=ab12\n@daily echo d\nbad line\n"
    entries = parse_crontab(text)
    assert [(e.schedule, e.job_id) for e in entries] == [("0 0 * * 5", "ab12"), ("0 0 * * *", None)]


def test_due_matches_only_the_right_minute():
    entries = parse_crontab("0 0 * * 5 echo friday\n*/15 * * * * echo quarter\n")
    friday_midnight = datetime(2026, 10, 30, 0, 0)
    assert [e.command for e in due(entries, friday_midnight)] == ["echo friday", "echo quarter"]
    assert [e.command for e in due(entries, friday_midnight + timedelta(minutes=1))] == []


def test_daemon_really_runs_an_agent_job(manager, user_crontab, tmp_path):
    proof = (tmp_path / "proof.txt").as_posix()
    change = manager.apply(manager.plan_add(["* * * * *"], f"echo ran at $(date +%H:%M) >> {proof}"))
    daemon = Daemon(manager, log=lambda message: None)
    procs = daemon.tick(datetime.now().replace(second=0, microsecond=0))
    assert len(procs) == 1                      # the user's 03:15 line is not due now
    for p in procs:
        p.wait(timeout=60)
    assert (tmp_path / "proof.txt").read_text().startswith("ran at ")   # % was unescaped
    assert manager.log_path(change.job_id).exists()                    # output redirect worked
    daemon.reap()
    assert "exit code 0" in daemon.log_path.read_text()


def test_status_uses_the_heartbeat(agent_env):
    home = agent_env["home"]
    home.mkdir(parents=True, exist_ok=True)
    state = {"pid": 1, "started": "2026-10-27T10:00:00", "runs": 3,
             "heartbeat": datetime.now().isoformat(timespec="seconds")}
    (home / "daemon.json").write_text(json.dumps(state))
    assert status(home)["running"]
    state["heartbeat"] = (datetime.now() - timedelta(minutes=5)).isoformat(timespec="seconds")
    (home / "daemon.json").write_text(json.dumps(state))
    assert not status(home)["running"]
