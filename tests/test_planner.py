"""LLM planner recovery path, with a scripted fake model (no network)."""

import asyncio

from cron_agent.planner import LLMPlanner
from cron_agent.providers import ToolCall


class ScriptedModel:
    def __init__(self, replies):
        self.replies, self.prompts = list(replies), []

    async def complete(self, system, user, tools):
        self.prompts.append(user)
        return self.replies.pop(0)


def planner_with(replies):
    p = LLMPlanner.__new__(LLMPlanner)
    p.name, p.provider, p.tools = "fake", ScriptedModel(replies), []
    return p


def test_invalid_schedule_is_retried_with_the_real_error():
    p = planner_with([ToolCall("add_job", {"schedule": "every friday", "command": "echo hi"}),
                      ToolCall("add_job", {"schedule": "0 0 * * 5", "command": "echo hi"})])
    plan = asyncio.run(p.plan("every friday at midnight run `echo hi`"))
    assert plan.tool == "add_job" and plan.args["schedules"] == ["0 0 * * 5"]
    assert plan.meta == {"attempts": 2, "first_error": plan.meta["first_error"], "recovered_by": "retry"}
    assert "schedule" in plan.meta["first_error"] and "expected 5 fields" in p.provider.prompts[1]


def test_unsafe_command_from_the_model_never_becomes_a_plan():
    bad = ToolCall("add_job", {"schedule": "0 3 * * *", "command": "rm -rf /"})
    p = planner_with([bad, bad])
    plan = asyncio.run(p.plan("every day at 3am run `rm -rf /`"))
    assert plan.meta["recovered_by"] == "rules"          # model failed twice: rules take over
    assert "blocked" in plan.meta["first_error"]


def test_hybrid_uses_rules_first_and_llm_only_when_they_cannot_read_it():
    from cron_agent.planner import HybridPlanner

    llm = planner_with([ToolCall("add_job", {"schedule": "0 9,21 * * *", "command": "~/bin/x.sh"})])
    hybrid = HybridPlanner(llm=llm, llm_name="fake")
    plain = asyncio.run(hybrid.plan("every day at 9am run ~/bin/x.sh"))
    assert plain.args["schedules"] == ["0 9 * * *"] and llm.provider.prompts == []   # rules only
    odd = asyncio.run(hybrid.plan("twice a day run ~/bin/x.sh"))
    assert odd.args["schedules"] == ["0 9,21 * * *"] and odd.meta["planned_by"] == "fake"
    assert "Check the preview" in odd.notes[0]


def test_hybrid_keeps_deliberate_questions():
    from cron_agent.planner import HybridPlanner

    llm = planner_with([])                       # would fail if it were called
    hybrid = HybridPlanner(llm=llm, llm_name="fake")
    for text in ("every day run ~/bin/x.sh", "every 30 seconds run ~/bin/x.sh"):
        assert asyncio.run(hybrid.plan(text)).kind == "ask"


def test_text_reply_falls_back_to_rules():
    p = planner_with(["Sure! Use 0 0 * * 5.", "0 0 * * 5"])
    plan = asyncio.run(p.plan("run ~/x.sh every friday at midnight"))
    assert plan.args["schedules"] == ["0 0 * * 5"]
    assert plan.meta["recovered_by"] == "rules"
