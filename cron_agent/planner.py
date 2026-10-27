"""Orchestration: turn a request into exactly one validated tool call.

RulesPlanner needs nothing but Python. LLMPlanner sends the request, the current
jobs and the available scripts (context) plus the tool list discovered from the
MCP server to a model, validates the JSON it returns with pydantic, retries once,
and falls back to the rules if the model still gets it wrong.
"""

from __future__ import annotations

import copy
import os

import httpx
from pydantic import BaseModel, ValidationError, field_validator

from . import translator
from .cronexpr import CronExpr
from .providers import ProviderError, ToolCall, make_provider
from .safety import check_command
from .translator import Plan

# Parameters only the client sets; the model never sees them.
HIDDEN = {"dry_run", "base_hash", "count", "lines", "backup_id", "job_id@add_job"}

ASK_USER = {
    "name": "ask_user",
    "description": "Ask the user one short question when the time, frequency or command is unclear.",
    "parameters": {"type": "object", "properties": {"question": {"type": "string"}},
                   "required": ["question"], "additionalProperties": False},
}

SYSTEM_PROMPT = """You are the planner for cron-agent, which manages jobs in the user's own Linux crontab.
Answer every request with exactly one tool call and no other text.

- add_job.schedule is a standard 5-field cron expression: minute hour day-of-month month day-of-week (0 = Sunday). Separate two expressions with "; ".
- Use the command exactly as the user gave it, or a script path from the list of available scripts. Never invent a shell command.
- When both day-of-month and day-of-week are restricted, cron runs the job if EITHER matches. Avoid that unless the user asked for it.
- If the time, the frequency or the command is unclear, call ask_user with one short question.
- To remove a job or read its log, use its id from the list of current jobs.
"""


class AddJobArgs(BaseModel):
    schedule: str
    command: str

    @field_validator("schedule")
    @classmethod
    def _valid_schedule(cls, v: str) -> str:
        parts = [s.strip() for s in v.split(";") if s.strip()]
        if not parts:
            raise ValueError("schedule is empty")
        return "; ".join(str(CronExpr.parse(p)) for p in parts)

    @field_validator("command")
    @classmethod
    def _safe_command(cls, v: str) -> str:
        return check_command(v)


class JobIdArgs(BaseModel):
    job_id: str


class ScheduleArgs(BaseModel):
    schedule: str

    @field_validator("schedule")
    @classmethod
    def _valid(cls, v: str) -> str:
        return "; ".join(str(CronExpr.parse(p)) for p in v.split(";") if p.strip())


class QueryArgs(BaseModel):
    query: str


class QuestionArgs(BaseModel):
    question: str


class NoArgs(BaseModel):
    pass


VALIDATORS = {
    "add_job": AddJobArgs, "remove_job": JobIdArgs, "read_job_log": JobIdArgs,
    "preview_schedule": ScheduleArgs, "find_script": QueryArgs, "ask_user": QuestionArgs,
    "list_jobs": NoArgs, "cron_status": NoArgs, "rollback": NoArgs,
}


def llm_tool_specs(mcp_tools) -> list[dict]:
    """The model's view of the MCP tools: client-only parameters removed, every field required."""
    specs = []
    for tool in mcp_tools:
        raw = getattr(tool, "input_schema", None) or getattr(tool, "inputSchema", None)
        schema = copy.deepcopy(raw or {"type": "object", "properties": {}})
        props = {k: v for k, v in schema.get("properties", {}).items()
                 if k not in HIDDEN and f"{k}@{tool.name}" not in HIDDEN}
        for prop in props.values():
            prop.pop("default", None)
            prop.pop("title", None)
        specs.append({"name": tool.name, "description": tool.description or "",
                      "parameters": {"type": "object", "properties": props,
                                     "required": sorted(props), "additionalProperties": False}})
    return specs + [ASK_USER]


def to_plan(call: ToolCall) -> Plan:
    if call.name not in VALIDATORS:
        raise ValueError(f"unknown tool {call.name!r}")
    args = VALIDATORS[call.name](**call.args)
    if call.name == "ask_user":
        return Plan("ask", message=args.question, pending="answer")
    if call.name == "add_job":
        return Plan("tool", "add_job", {"schedules": args.schedule.split("; "),
                                        "command": args.command, "guard": ""})
    if call.name in ("remove_job", "read_job_log"):
        return Plan("tool", call.name, {"ref": {"ids": [args.job_id], "words": None}})
    return Plan("tool", call.name, args.model_dump())


class RulesPlanner:
    name = "rules"

    async def plan(self, text: str, context: str = "") -> Plan:
        return translator.plan(text)


def detect_llm() -> str | None:
    """Pick an available model: Claude if a key and the SDK are present, else a running Ollama."""
    if os.environ.get("ANTHROPIC_API_KEY"):
        try:
            import anthropic  # noqa: F401

            return "anthropic"
        except ImportError:
            pass
    host = os.environ.get("OLLAMA_HOST", "http://localhost:11434").rstrip("/")
    try:
        httpx.get(f"{host}/api/tags", timeout=1.0).raise_for_status()
        return "ollama"
    except httpx.HTTPError:
        return None


class HybridPlanner:
    """Rules first (instant, offline, predictable); the LLM only reads what the rules can't.

    The rules still own every deliberate question ("At what time?", "cron can't do
    every 30 seconds"); the model is consulted only when no schedule was recognised.
    """

    def __init__(self, mcp_tools=None, llm=None, llm_name: str | None = None):
        self.rules = RulesPlanner()
        if llm is None:
            llm_name = llm_name or detect_llm()
            llm = LLMPlanner(llm_name, mcp_tools or []) if llm_name else None
        self.llm, self.llm_name = llm, llm_name or getattr(llm, "name", None)
        self.name = f"hybrid (rules + {self.llm_name})" if self.llm else "rules (no LLM found)"

    async def plan(self, text: str, context: str = "") -> Plan:
        first = await self.rules.plan(text)
        if first.kind == "tool" or self.llm is None or first.pending != "frequency":
            return first
        second = await self.llm.plan(text, context)
        if second.kind == "tool" and (second.meta or {}).get("recovered_by") != "rules":
            second.notes.insert(0, f"The rules didn’t recognise this schedule, so {self.llm_name} "
                                   "planned it. Check the preview carefully.")
            second.meta = {**(second.meta or {}), "planned_by": self.llm_name}
            return second
        return first


class LLMPlanner:
    def __init__(self, provider_name: str, mcp_tools):
        self.name = provider_name
        self.provider = make_provider(provider_name)
        self.tools = llm_tool_specs(mcp_tools)

    async def plan(self, text: str, context: str = "") -> Plan:
        """Ask the model; on an invalid answer retry once with the error, then fall back to rules.

        plan.meta records the path taken: attempts, the first error, and how it recovered.
        """
        user = f"{context}\n\nRequest: {text}" if context else text
        error, first_error = None, None
        for attempt in (1, 2):
            prompt = user if error is None else f"{user}\n\nYour last answer was rejected: {error}"
            try:
                reply = await self.provider.complete(SYSTEM_PROMPT, prompt, self.tools)
                if isinstance(reply, str):
                    raise ValueError("answer with a tool call, not text")
                result = to_plan(reply)
                result.meta = {"attempts": attempt, "first_error": first_error,
                               "recovered_by": "retry" if attempt == 2 else None}
                return result
            except ProviderError as exc:
                fallback = translator.plan(text)
                fallback.notes.insert(0, f"{self.name} unavailable ({exc}); used the offline rules.")
                fallback.meta = {"attempts": attempt, "first_error": str(exc),
                                 "recovered_by": "rules", "provider_error": True}
                return fallback
            except ValidationError as exc:
                # tell the model exactly which field was wrong and why, not just "1 validation error"
                error = "; ".join(f"{'.'.join(map(str, e['loc'])) or 'arguments'}: "
                                  f"{e['msg'].removeprefix('Value error, ')}" for e in exc.errors())
                first_error = first_error or error
            except (ValueError, TypeError, KeyError) as exc:
                error = str(exc).splitlines()[0]
                first_error = first_error or error
        fallback = translator.plan(text)
        fallback.notes.insert(0, f"{self.name} gave an invalid tool call ({error}); "
                                 "used the offline rules instead.")
        fallback.meta = {"attempts": 2, "first_error": first_error, "recovered_by": "rules"}
        return fallback
