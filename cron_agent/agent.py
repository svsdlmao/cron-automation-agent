"""The user-to-shell loop: plan -> (preview -> approve) -> tool call -> feedback.

Read tools run straight away. Write tools always run as a dry run first; the
user sees the exact crontab change and next run times and must type y before
the real call is made. This gate lives here, in the client, so no model output
can skip it.
"""

from __future__ import annotations

import re
import shlex
from datetime import datetime, timedelta
from typing import Awaitable, Callable

from .client import ToolClient, ToolError
from .scripts import tokens
from .translator import Plan

Ask = Callable[[str], Awaitable[str]]
WRITE_TOOLS = {"add_job", "remove_job", "rollback"}


def _bullets(lines: list[str], mark: str) -> str:
    return "\n".join(f"  {mark} {ln}" for ln in lines)


RUN_FORMAT = "%a %d %b %Y %H:%M"


def effective_runs(guard: str, runs: list[str]) -> list[str]:
    """Apply the shell date check in a guard to cron's wake-up times."""
    keep = []
    for text in runs:
        when = datetime.strptime(text, RUN_FORMAT)
        if "%u" in guard:
            ok = when.isoweekday() == int(re.search(r"= (\d) \]", guard).group(1))
        elif "tomorrow" in guard:
            ok = (when + timedelta(days=1)).day == 1
        elif "+7 days" in guard:
            ok = (when + timedelta(days=7)).day <= 7
        else:
            ok = True
        if ok:
            keep.append(text)
    return keep


class Agent:
    def __init__(self, tools: ToolClient, planner, ask: Ask):
        self.tools = tools
        self.planner = planner
        self.ask = ask
        self.pending_text: str | None = None
        self.pending_kind: str | None = None

    # ------------------------------------------------------------ entry point

    async def handle(self, line: str) -> str:
        text = line.strip()
        if not text:
            return ""
        context = await self._context()
        if self.pending_kind:
            if text.lower() in {"cancel", "never mind", "nevermind", "stop", "n", "no"}:
                self.pending_text = self.pending_kind = None
                return "OK, cancelled. Nothing changed."
            alone = await self.planner.plan(text, context)
            if alone.kind == "tool":            # a complete new request, not an answer
                self.pending_text = self.pending_kind = None
                plan = alone
            else:
                text = self._merge_answer(text)
                plan = await self.planner.plan(text, context)
        else:
            plan = await self.planner.plan(text, context)
        if plan.kind == "ask":
            self.pending_text, self.pending_kind = text, plan.pending
            return self._notes(plan) + plan.message
        if plan.kind == "reply":
            return plan.message
        try:
            return await self._run(plan)
        except ToolError as exc:
            return f"Not done: {exc}"

    def _merge_answer(self, answer: str) -> str:
        original, kind = self.pending_text or "", self.pending_kind
        self.pending_text = self.pending_kind = None
        if kind == "time":
            return f"{original} at {answer}"
        if kind == "command":
            return f"{original} `{answer}`"
        return f"{original} {answer}"

    async def _context(self) -> str:
        if getattr(self.planner, "name", "rules") == "rules":
            return ""
        jobs = (await self.tools.call("list_jobs"))["jobs"]
        scripts = (await self.tools.call("find_script", {"query": ""}))["matches"]
        job_lines = [f"- {j['id']}: {' ; '.join(j['schedules'])} {j['command']}" for j in jobs]
        script_lines = [f"- {s['path']}" for s in scripts]
        return ("Current jobs:\n" + ("\n".join(job_lines) or "- none") +
                "\nAvailable scripts:\n" + ("\n".join(script_lines) or "- none"))

    # ------------------------------------------------------------ dispatch

    async def _run(self, plan: Plan) -> str:
        tool, args = plan.tool, plan.args
        if tool == "list_jobs":
            return self._format_jobs(await self.tools.call("list_jobs"))
        if tool == "cron_status":
            s = await self.tools.call("cron_status")
            return (f"cron daemon: {s['daemon']}\nbackend: {s['backend']} on {s['platform']}\n"
                    f"agent jobs: {s['agent_jobs']} · other crontab lines: {s['other_lines']} · "
                    f"backups: {s['backups']}")
        if tool == "preview_schedule":
            data = await self.tools.call("preview_schedule", {"schedule": args["schedule"]})
            return "\n".join(self._format_preview(p) for p in data["previews"])
        if tool == "find_script":
            data = await self.tools.call("find_script", {"query": args.get("query", "")})
            if not data["matches"]:
                return "No scripts found in " + ", ".join(data["searched"])
            return "Scripts:\n" + "\n".join(f"  {m['display']}" for m in data["matches"])
        if tool == "read_job_log":
            job = await self._resolve_job(args.get("ref", {}))
            if isinstance(job, str):
                return job
            data = await self.tools.call("read_job_log", {"job_id": job["id"]})
            if data["empty"]:
                return f"Job {job['id']} has no output yet (it may not have run)."
            return f"Last output of job {job['id']}:\n{data['log']}"
        if tool == "add_job":
            return await self._add(plan)
        if tool == "remove_job":
            job = await self._resolve_job(args.get("ref", {}))
            if isinstance(job, str):
                return job
            return await self._write("remove_job", {"job_id": job["id"]}, "Remove this job?")
        if tool == "rollback":
            return await self._write("rollback", {}, "Restore this backup?")
        return f"I don’t know how to {tool}."

    # ------------------------------------------------------------ writes

    async def _add(self, plan: Plan) -> str:
        args = plan.args
        notes = list(plan.notes)
        command = args.get("command")
        if not command:
            found = (await self.tools.call("find_script", {"query": args["command_query"]}))
            matches = found["matches"]
            if not matches:
                self.pending_text, self.pending_kind = None, None
                return (f"I couldn’t find a script matching “{args['command_query']}” in "
                        f"{', '.join(found['searched'])}. Say the command in `backticks`, "
                        "or give the script’s path.")
            if len(matches) > 1 and matches[0]["score"] - matches[1]["score"] < 0.2:
                options = "\n".join(f"  {m['display']}" for m in matches[:5])
                return f"More than one script matches “{args['command_query']}”:\n{options}\n" \
                       "Say which one, with its path."
            command = shlex.quote(matches[0]["path"])
            notes.append(f"Using {matches[0]['display']} (matched “{args['command_query']}”).")
        guard = args.get("guard") or ""
        command = guard + command
        call = {"schedule": "; ".join(args["schedules"]), "command": command}
        effective = None
        if guard:  # show the days the job really runs, not every day cron wakes it
            raw = await self.tools.call("preview_schedule", {"schedule": call["schedule"], "count": 60})
            effective = effective_runs(guard, raw["previews"][0]["next_runs"])[:3]
        return await self._write("add_job", call, "Add this job?", notes, effective)

    async def _write(self, tool: str, call: dict, question: str, notes: list[str] | None = None,
                     effective: list[str] | None = None) -> str:
        preview = await self.tools.call(tool, {**call, "dry_run": True})
        if effective is not None:
            for p in preview.get("previews", []):
                p["next_runs"] = effective
                p["description"] += ", only when the date check passes"
        print(self._format_change(preview, notes or [], proposed=True))
        answer = (await self.ask(f"{question} [y/N] ")).strip().lower()
        if answer not in {"y", "yes"}:
            return "Cancelled. Nothing changed."
        final = {**call, "base_hash": preview["base_hash"]}
        if tool == "add_job":
            final["job_id"] = preview["job_id"]
        if tool == "rollback":
            final["backup_id"] = preview["backup_id"]
        result = await self.tools.call(tool, final)
        if effective is not None:
            for p in result.get("previews", []):
                p["next_runs"] = effective
        return self._format_result(result)

    # ------------------------------------------------------------ lookups

    async def _resolve_job(self, ref: dict) -> dict | str:
        jobs = (await self.tools.call("list_jobs"))["jobs"]
        if not jobs:
            return "There are no agent jobs yet."
        for job in jobs:
            if job["id"] in (ref.get("ids") or []):
                return job
        words = tokens(ref.get("words") or "")
        if words:
            scored = sorted(((len(words & tokens(j["command"])), j) for j in jobs),
                            key=lambda pair: -pair[0])
            best = [j for score, j in scored if score and score == scored[0][0]]
            if len(best) == 1:
                return best[0]
        if len(jobs) == 1 and not ref.get("ids") and not words:
            return jobs[0]
        listing = "\n".join(f"  {j['id']}  {j['command']}" for j in jobs)
        return f"Which job? Say its id:\n{listing}"

    # ------------------------------------------------------------ formatting

    @staticmethod
    def _notes(plan: Plan) -> str:
        return "".join(f"Note: {n}\n" for n in plan.notes)

    @staticmethod
    def _format_preview(p: dict) -> str:
        out = [f"{p['schedule']}  =  {p['description']}",
               "  next runs: " + " · ".join(p["next_runs"])]
        out += [f"  warning: {w}" for w in p.get("warnings", [])]
        return "\n".join(out)

    def _format_change(self, change: dict, notes: list[str], proposed: bool) -> str:
        title = {"add": "Proposed new job", "remove": "Proposed removal",
                 "rollback": "Proposed rollback"}[change["action"]]
        if change.get("job_id"):
            title += f" {change['job_id']}"
        if change["action"] == "rollback":
            title += f" to backup {change['backup_id']}"
        parts = [title]
        if change["removed"]:
            parts.append(_bullets(change["removed"], "-"))
        if change["added"]:
            parts.append(_bullets(change["added"], "+"))
        if not change["added"] and not change["removed"]:
            parts.append("  (no difference)")
        for p in change.get("previews", []):
            parts.append("  meaning:   " + p["description"])
            parts.append("  next runs: " + " · ".join(p["next_runs"]))
            parts += [f"  warning:   {w}" for w in p.get("warnings", [])]
        parts += [f"  note:      {n}" for n in notes]
        return "\n".join(parts)

    @staticmethod
    def _format_result(change: dict) -> str:
        action = change["action"]
        if action == "add":
            first = change["previews"][0]["next_runs"][0] if change.get("previews") else "?"
            return (f"Installed job {change['job_id']}. Next run: {first}. "
                    f"Backup {change['backup_id']} saved; say “undo” to revert.")
        if action == "remove":
            return f"Removed job {change['job_id']}. Backup {change['backup_id']} saved; say “undo” to revert."
        return f"Restored backup {change['backup_id']}."

    @staticmethod
    def _format_jobs(data: dict) -> str:
        jobs = data["jobs"]
        if not jobs:
            head = "No agent jobs yet."
        else:
            rows = [f"  {j['id']}  {' ; '.join(j['schedules']):<16}  {j['command']}\n"
                    f"        {j['description']}" for j in jobs]
            head = f"{len(jobs)} agent job(s):\n" + "\n".join(rows)
        if data["other_lines"]:
            head += f"\n({data['other_lines']} other crontab line(s) left untouched)"
        return head
