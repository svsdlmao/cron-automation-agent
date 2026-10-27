"""End-to-end over the real MCP stdio transport: client process <-> cron-tools server process."""

import asyncio

import pytest

from cron_agent.client import ToolClient, ToolError

from .conftest import USER_LINES

EXPECTED_TOOLS = {"list_jobs", "preview_schedule", "cron_status", "read_job_log", "find_script",
                  "add_job", "remove_job", "rollback"}


def run(coro):
    return asyncio.run(coro)


def test_server_exposes_exactly_the_cron_tools(agent_env):
    async def go():
        async with ToolClient.start() as tools:
            return set(tools.tool_names)

    assert run(go()) == EXPECTED_TOOLS


def test_two_phase_write_over_mcp(agent_env, user_crontab):
    async def go():
        async with ToolClient.start() as tools:
            preview = await tools.call("add_job", {"schedule": "0 0 * * 5",
                                                   "command": "~/scripts/db_backup.sh",
                                                   "dry_run": True})
            assert not preview["applied"]
            assert user_crontab.read_text() == USER_LINES          # dry run wrote nothing
            assert "Friday" in preview["previews"][0]["description"]

            done = await tools.call("add_job", {"schedule": "0 0 * * 5",
                                                "command": "~/scripts/db_backup.sh",
                                                "job_id": preview["job_id"],
                                                "base_hash": preview["base_hash"]})
            assert done["applied"] and done["backup_id"]
            listed = await tools.call("list_jobs")
            assert [j["id"] for j in listed["jobs"]] == [preview["job_id"]]
            assert listed["other_lines"] == 2

            removed = await tools.call("remove_job", {"job_id": preview["job_id"]})
            assert removed["applied"]
            assert user_crontab.read_text() == USER_LINES

            back = await tools.call("rollback", {})
            assert back["applied"]
            assert len((await tools.call("list_jobs"))["jobs"]) == 1

    run(go())


def test_server_refuses_bad_input(agent_env, user_crontab):
    async def go():
        async with ToolClient.start() as tools:
            with pytest.raises(ToolError, match="blocked"):
                await tools.call("add_job", {"schedule": "0 0 * * 5", "command": "rm -rf /"})
            with pytest.raises(ToolError, match="outside"):
                await tools.call("add_job", {"schedule": "61 0 * * 5", "command": "echo hi"})
            with pytest.raises(ToolError, match="changed since the preview"):
                await tools.call("add_job", {"schedule": "0 0 * * 5", "command": "echo hi",
                                             "base_hash": "0" * 16})
        assert user_crontab.read_text() == USER_LINES

    run(go())


def test_read_tools(agent_env, user_crontab):
    async def go():
        async with ToolClient.start() as tools:
            status = await tools.call("cron_status")
            assert status["backend"] == "file"
            found = await tools.call("find_script", {"query": "database backup"})
            assert found["matches"][0]["path"].endswith("db_backup.sh")
            preview = await tools.call("preview_schedule", {"schedule": "*/15 9-16 * * 1-5"})
            assert len(preview["previews"][0]["next_runs"]) == 5
            log = await tools.call("read_job_log", {"job_id": "abcd"})
            assert log["empty"]

    run(go())
