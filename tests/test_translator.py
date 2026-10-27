import pytest

from cron_agent import translator
from cron_agent.evaluate import DEFAULT_SET, load_cases, score

CASES = load_cases(DEFAULT_SET)


@pytest.mark.parametrize("case", CASES, ids=[f"{c['id']}-{c['cat']}" for c in CASES])
def test_labelled_prompt(case):
    plan = translator.plan(case["prompt"])
    verdict = score(case, plan)
    assert verdict["ok"], verdict["why"]


def test_first_monday_adds_a_weekday_guard():
    plan = translator.plan("back up my database on the first Monday of every month at 9am")
    assert plan.args["schedules"] == ["0 9 1-7 * *"]
    assert plan.args["guard"].startswith('[ "$(date +%u)" = 1 ]')
    assert any("ORs" in n for n in plan.notes)


def test_assumptions_are_reported():
    plan = translator.plan("every morning run ~/bin/x.sh")
    assert plan.args["schedules"] == ["0 9 * * *"]
    assert any("Assumed 09:00" in n for n in plan.notes)


def test_command_path_keeps_case_and_arguments():
    plan = translator.plan("run /opt/Tools/Backup.sh --full every day at 1am")
    assert plan.args["command"] == "/opt/Tools/Backup.sh --full"


def test_words_inside_the_command_do_not_change_the_intent():
    # found in the demo: ">> /tmp/health.log" used to be read as "show me the log"
    plan = translator.plan("every 15 minutes during working hours on weekdays run "
                           "`echo ok >> /tmp/health.log`")
    assert plan.tool == "add_job"
    assert plan.args["schedules"] == ["*/15 9-16 * * 1-5"]


def test_query_has_no_stray_punctuation():
    plan = translator.plan("Run my database backup script every Friday at midnight.")
    assert plan.args["command_query"] == "database backup"


def test_quoted_cron_is_not_mistaken_for_a_command():
    plan = translator.plan('explain "*/5 * * * *"')
    assert plan.tool == "preview_schedule"
