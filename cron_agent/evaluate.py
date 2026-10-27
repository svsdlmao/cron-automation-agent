"""Score a planner on the labelled prompt set: `python -m cron_agent eval [--provider ollama]`.

A schedule counts as correct when it fires at exactly the same times as the
expected one over the next 60 runs, so `0 0 * * 5` and `0 0 * * fri` both pass.
For LLM planners a case only counts when the model itself answered correctly;
falling back to the offline rules is scored as a miss.
"""

from __future__ import annotations

import asyncio
import json
import statistics
import time
from collections import defaultdict
from datetime import datetime
from pathlib import Path

from croniter import croniter

from . import config
from .planner import HybridPlanner, LLMPlanner, RulesPlanner
from .scripts import tokens
from .translator import Plan

DEFAULT_SET = Path(__file__).resolve().parents[1] / "eval" / "prompts.jsonl"
START = datetime(2026, 1, 1)
EVAL_CONTEXT = """Current jobs:
- a1f3: 0 0 * * 5 /home/demo/scripts/db_backup.sh
Available scripts:
- /home/demo/scripts/db_backup.sh
- /home/demo/scripts/cleanup_tmp.sh
- /home/demo/scripts/weekly_report.py
- /home/demo/bin/health_check.sh"""


def load_cases(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.startswith("//")]


def fire_times(exprs: list[str], n: int = 60) -> list[datetime]:
    times: set[datetime] = set()
    for expr in exprs:
        it = croniter(expr, START)
        times.update(it.get_next(datetime) for _ in range(n))
    return sorted(times)[:n]


def guard_kind(guard: str) -> str | None:
    if not guard:
        return None
    if "%u" in guard:
        return "weekday:" + guard.split("=")[1].split("]")[0].strip()
    if "tomorrow" in guard:
        return "last-day"
    if "+7 days" in guard:
        return "last-weekday"
    return "other"


def error_kind(message: str | None) -> str | None:
    """Group a rejected model answer into a few readable categories."""
    if not message:
        return None
    m = message.lower()
    if "not text" in m:
        return "text instead of a tool call"
    if "unknown tool" in m:
        return "unknown tool name"
    if "field required" in m or "missing" in m:
        return "missing argument"
    if m.startswith("schedule") or any(s in m for s in ("expected 5 fields", "not valid cron")):
        return "invalid cron syntax"
    if m.startswith("command"):
        return "unsafe or empty command"
    return "other invalid output"


def score(case: dict, plan: Plan, allow_fallback: bool = False) -> dict:
    """Return {'ok': bool, 'schedule_ok': bool|None, 'why': str}.

    allow_fallback=True scores what the system as a whole produced (model or rules);
    the default scores the model alone, counting a fallback as a miss.
    """
    expect = case["expect"]
    kind = expect["kind"]
    fell_back = any("used the offline rules" in n for n in plan.notes)
    if fell_back and not allow_fallback:
        return {"ok": False, "schedule_ok": False if kind == "add" else None,
                "why": "model failed; fell back to rules"}
    if kind in ("ask", "reply"):
        ok = plan.kind == kind or (kind == "reply" and plan.kind == "ask")
        return {"ok": ok, "schedule_ok": None, "why": "" if ok else f"got {plan.kind} {plan.tool}"}
    if kind == "tool":
        ok = plan.kind == "tool" and plan.tool == expect["tool"]
        return {"ok": ok, "schedule_ok": None, "why": "" if ok else f"got {plan.kind} {plan.tool}"}

    if plan.kind != "tool" or plan.tool != "add_job":
        return {"ok": False, "schedule_ok": False, "why": f"got {plan.kind} {plan.tool or ''}".strip()}
    try:
        schedule_ok = fire_times(plan.args["schedules"]) == fire_times(expect["schedules"])
    except Exception as exc:  # an invalid expression from a model
        return {"ok": False, "schedule_ok": False, "why": f"invalid schedule: {exc}"}
    why = [] if schedule_ok else [f"schedule {plan.args['schedules']} != {expect['schedules']}"]
    if guard_kind(plan.args.get("guard", "")) != expect.get("guard"):
        why.append(f"guard {guard_kind(plan.args.get('guard', ''))} != {expect.get('guard')}")
    if "command" in expect:
        got = plan.args.get("command", "")
        if expect["command"] not in got:
            why.append(f"command {got!r}")
    if "query" in expect:
        wanted = tokens(expect["query"])
        got = tokens(plan.args.get("command_query", "") + " " + plan.args.get("command", ""))
        if not wanted <= got:
            why.append(f"command target {plan.args.get('command_query') or plan.args.get('command')!r}")
    return {"ok": not why, "schedule_ok": schedule_ok, "why": "; ".join(why)}


async def run_eval(path: Path, provider: str) -> dict:
    cases = load_cases(path)
    if provider == "rules":
        planner = RulesPlanner()
    else:
        from .client import ToolClient

        async with ToolClient.start() as tools:
            planner = (HybridPlanner(tools.tools) if provider == "hybrid"
                       else LLMPlanner(provider, tools.tools))
    results, latencies = [], []
    for case in cases:
        started = time.perf_counter()
        plan = await planner.plan(case["prompt"], "" if provider == "rules" else EVAL_CONTEXT)
        latencies.append(time.perf_counter() - started)
        verdict = score(case, plan)
        end_to_end = score(case, plan, allow_fallback=True)["ok"]
        results.append({"id": case["id"], "cat": case["cat"], "prompt": case["prompt"],
                        "got": {"kind": plan.kind, "tool": plan.tool, "args": plan.args},
                        "meta": plan.meta, "end_to_end_ok": end_to_end,
                        "latency_s": round(latencies[-1], 3), **verdict})
    by_cat: dict[str, list[bool]] = defaultdict(list)
    for r in results:
        by_cat[r["cat"]].append(r["ok"])
    adds = [r for r in results if r["schedule_ok"] is not None]
    invalid = [r for r in results if (r["meta"] or {}).get("first_error")]
    kinds: dict[str, int] = defaultdict(int)
    for r in invalid:
        kinds[error_kind(r["meta"]["first_error"])] += 1
    recovery = {
        "invalid_first_answers": len(invalid),
        "recovered_by_retry": sum(r["meta"].get("recovered_by") == "retry" for r in invalid),
        "recovered_by_rules": sum(r["meta"].get("recovered_by") == "rules" for r in invalid),
        "correct_after_recovery": sum(r["end_to_end_ok"] for r in invalid),
        "error_kinds": dict(sorted(kinds.items(), key=lambda kv: -kv[1])),
    }
    return {
        "recovery": recovery,
        "end_to_end_correct": sum(r["end_to_end_ok"] for r in results),
        "p95_latency_s": round(sorted(latencies)[max(0, int(0.95 * len(latencies)) - 1)], 3),
        "provider": provider,
        "model": None if provider == "rules" else getattr(planner, "name", None) if provider == "hybrid"
        else config.model_name({"ollama": "llama3.2", "anthropic": "claude-opus-5-5",
                                "openai": "gpt-4o-mini"}[provider]),
        "cases": len(results),
        "correct": sum(r["ok"] for r in results),
        "schedule_correct": sum(bool(r["schedule_ok"]) for r in adds),
        "schedule_cases": len(adds),
        "median_latency_s": round(statistics.median(latencies), 3),
        "by_category": {c: [sum(v), len(v)] for c, v in sorted(by_cat.items())},
        "results": results,
        "date": datetime.now().isoformat(timespec="seconds"),
    }


def report_md(summary: dict) -> str:
    pct = 100 * summary["correct"] / summary["cases"]
    lines = [f"# Evaluation: {summary['provider']}" + (f" ({summary['model']})" if summary["model"] else ""),
             "", f"Run {summary['date']}", "",
             f"- Fully correct: **{summary['correct']}/{summary['cases']} ({pct:.0f}%)**",
             f"- Schedule correct on scheduling prompts: {summary['schedule_correct']}/"
             f"{summary['schedule_cases']}",
             f"- Median planning time: {summary['median_latency_s']} s "
             f"(95th percentile {summary['p95_latency_s']} s)"]
    rec = summary["recovery"]
    if rec["invalid_first_answers"]:
        n = rec["invalid_first_answers"]
        lines += [f"- System result after recovery (model or rules): {summary['end_to_end_correct']}/"
                  f"{summary['cases']}",
                  f"- Invalid first answers: {n} — recovered by retry {rec['recovered_by_retry']}, "
                  f"by falling back to rules {rec['recovered_by_rules']}; "
                  f"correct afterwards {rec['correct_after_recovery']}/{n}",
                  "- Kinds: " + ", ".join(f"{k} {v}" for k, v in rec["error_kinds"].items())]
    lines += ["", "| Category | Correct |", "|---|---|"]
    lines += [f"| {c} | {ok}/{n} |" for c, (ok, n) in summary["by_category"].items()]
    misses = [r for r in summary["results"] if not r["ok"]]
    if misses:
        lines += ["", "## Misses", "", "| id | prompt | why |", "|---|---|---|"]
        lines += [f"| {r['id']} | {r['prompt']} | {r['why']} |" for r in misses]
    return "\n".join(lines) + "\n"


def main(path: str | None = None, out: str | None = None) -> None:
    provider = config.provider_name()
    summary = asyncio.run(run_eval(Path(path) if path else DEFAULT_SET, provider))
    out_dir = Path(out) if out else DEFAULT_SET.parent / "results"
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / f"{provider}.json").write_text(json.dumps(summary, indent=2, default=str), encoding="utf-8")
    (out_dir / f"{provider}.md").write_text(report_md(summary), encoding="utf-8")
    print(report_md(summary))
