from datetime import datetime

import pytest

from cron_agent.cronexpr import CronError, CronExpr, equivalent


@pytest.mark.parametrize("text", [
    "0 0 * * 5", "*/15 9-16 * * 1-5", "0 9 1,15 * *", "0 0 1 jan *", "0 9 * * mon-fri",
    "30 1-22/3 * * *", "0 0 * * 7", "@daily", "@weekly",
])
def test_valid_expressions(text):
    CronExpr.parse(text)


@pytest.mark.parametrize("text, reason", [
    ("60 * * * *", "outside"), ("* 24 * * *", "outside"), ("* * 0 * *", "outside"),
    ("* * * 13 *", "outside"), ("* * * * 8", "outside"), ("5-1 * * * *", "backwards"),
    ("*/0 * * * *", "step"), ("* * *", "expected 5"), ("* * * * * *", "expected 5"),
    ("a b c d e", "not a number"), ("1/5 * * * *", "step needs"), ("* * * foo *", "not a number"),
    ("@reboot", "unsupported"),
])
def test_invalid_expressions(text, reason):
    with pytest.raises(CronError, match=reason):
        CronExpr.parse(text)


def test_next_runs_from_a_fixed_date():
    runs = CronExpr.parse("0 0 * * 5").next_runs(3, start=datetime(2026, 10, 22, 12, 0))
    assert runs == [datetime(2026, 10, 23), datetime(2026, 10, 30), datetime(2026, 11, 6)]


def test_english_read_back():
    assert "Friday" in CronExpr.parse("0 0 * * 5").describe()
    assert "00:00" in CronExpr.parse("0 0 * * 5").describe()


def test_both_day_fields_warning():
    expr = CronExpr.parse("0 9 1-7 * 1")
    assert expr.both_day_fields_restricted
    assert any("EITHER" in w for w in expr.warnings())


def test_equivalence_uses_fire_times():
    assert equivalent("0 0 * * 5", "0 0 * * fri")
    assert equivalent("0 0 * * 0", "0 0 * * 7")
    # the classic "first Monday" mistake fires on extra days
    assert not equivalent("0 9 1-7 * 1", "0 9 1-7 * *")
