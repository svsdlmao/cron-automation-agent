"""The safe-install core: snapshot, merge, tag, install through a pipe, verify, roll back.

Every write is two-phase. A dry run returns the exact change plus a hash of the
crontab it was computed from; the real call must present the same hash, so a
crontab that changed in between (another editor, another agent) is never
silently overwritten.
"""

from __future__ import annotations

import hashlib
import re
import secrets
import shlex
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path

from . import config, safety
from .cronexpr import CronExpr
from .crontab import Backend, CrontabError, FileCrontab, SystemCrontab, file_lock

TAG_RE = re.compile(r"\s#\s*cron-agent:id=([0-9a-f]{4,16})\s*$")
WRAP_RE = re.compile(r"^\{ (?P<cmd>.*); \} >> (?P<log>\S+) 2>&1$")
ID_RE = re.compile(r"^[0-9a-f]{4,16}$")
MAX_BACKUPS = 50


class ConflictError(CrontabError):
    """The crontab changed between the preview and the approved write."""


@dataclass
class Job:
    id: str
    schedules: list[str]
    command: str
    description: str
    lines: list[str] = field(default_factory=list)


@dataclass
class Change:
    action: str
    job_id: str | None
    base_hash: str
    added: list[str]
    removed: list[str]
    after: str
    backup_id: str | None = None
    applied: bool = False

    def public(self) -> dict:
        data = asdict(self)
        data.pop("after")
        return data


def digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def _normalise(text: str) -> str:
    lines = [ln.rstrip() for ln in text.replace("\r\n", "\n").split("\n")]
    while lines and not lines[-1]:
        lines.pop()
    return "\n".join(lines) + ("\n" if lines else "")


def _split_schedule(line: str) -> tuple[str, str]:
    if line.startswith("@"):
        macro, _, rest = line.partition(" ")
        return macro, rest
    parts = line.split(None, 5)
    return " ".join(parts[:5]), parts[5] if len(parts) > 5 else ""


class JobManager:
    def __init__(self, backend: Backend, home: Path):
        self.backend = backend
        self.home = home
        self.backup_dir = home / "backups"
        self.log_dir = home / "logs"
        self.lock_path = home / "crontab.lock"

    @classmethod
    def from_env(cls) -> "JobManager":
        home = config.agent_home()
        if config.backend_name() == "system":
            backend: Backend = SystemCrontab()
        else:
            backend = FileCrontab(home / "crontab.txt")
        return cls(backend, home)

    # ---------- reading ----------

    def read(self) -> str:
        return _normalise(self.backend.read())

    def list_jobs(self) -> list[Job]:
        jobs: dict[str, Job] = {}
        for line in self.read().splitlines():
            m = TAG_RE.search(line)
            if not m:
                continue
            job_id = m.group(1)
            schedule, body = _split_schedule(line[: m.start()].rstrip())
            wrapped = WRAP_RE.match(body)
            command = safety.unescape_percent(wrapped.group("cmd") if wrapped else body)
            job = jobs.setdefault(job_id, Job(job_id, [], command, ""))
            job.schedules.append(schedule)
            job.lines.append(line)
        for job in jobs.values():
            try:
                job.description = "; ".join(CronExpr.parse(s).describe() for s in job.schedules)
            except Exception:
                job.description = " | ".join(job.schedules)
        return list(jobs.values())

    def other_line_count(self) -> int:
        return sum(1 for ln in self.read().splitlines()
                   if ln.strip() and not ln.lstrip().startswith("#") and not TAG_RE.search(ln))

    # ---------- building ----------

    def log_path(self, job_id: str) -> Path:
        return self.log_dir / f"{job_id}.log"

    def build_lines(self, schedules: list[CronExpr], command: str, job_id: str) -> list[str]:
        command = safety.check_command(command)
        log = shlex.quote(self.log_path(job_id).as_posix())
        body = safety.escape_percent(f"{{ {command}; }} >> {log} 2>&1")
        return [f"{expr} {body} # cron-agent:id={job_id}" for expr in schedules]

    def _new_id(self, current: str) -> str:
        while True:
            job_id = secrets.token_hex(2)
            if f"cron-agent:id={job_id}" not in current:
                return job_id

    @staticmethod
    def _without(text: str, job_id: str) -> tuple[list[str], list[str]]:
        kept, removed = [], []
        for line in text.splitlines():
            m = TAG_RE.search(line)
            (removed if m and m.group(1) == job_id else kept).append(line)
        return kept, removed

    # ---------- planning ----------

    def plan_add(self, schedules: list[str], command: str, job_id: str | None = None) -> Change:
        exprs = [CronExpr.parse(s) for s in schedules]
        if not exprs:
            raise ValueError("at least one schedule is required")
        current = self.read()
        if job_id is not None and not ID_RE.match(job_id):
            raise ValueError("job_id must be 4-16 lowercase hex characters")
        job_id = job_id or self._new_id(current)
        kept, removed = self._without(current, job_id)
        added = self.build_lines(exprs, command, job_id)
        after = _normalise("\n".join(kept + added))
        return Change("add", job_id, digest(current), added, removed, after)

    def plan_remove(self, job_id: str) -> Change:
        current = self.read()
        kept, removed = self._without(current, job_id)
        if not removed:
            raise ValueError(f"no agent job with id {job_id!r}")
        return Change("remove", job_id, digest(current), [], removed, _normalise("\n".join(kept)))

    def plan_rollback(self, backup_id: str | None = None) -> Change:
        backups = self.list_backups()
        if not backups:
            raise ValueError("there are no backups to roll back to")
        backup_id = backup_id or backups[-1]
        if backup_id not in backups:
            raise ValueError(f"unknown backup {backup_id!r}")
        current = self.read()
        target = _normalise((self.backup_dir / f"{backup_id}.cron").read_text(encoding="utf-8"))
        before, after = set(current.splitlines()), set(target.splitlines())
        return Change("rollback", None, digest(current),
                      [ln for ln in target.splitlines() if ln not in before],
                      [ln for ln in current.splitlines() if ln not in after],
                      target, backup_id=backup_id)

    # ---------- applying ----------

    def apply(self, change: Change, base_hash: str | None = None) -> Change:
        """Snapshot, write through the backend, read back, and restore on any mismatch.

        The write only happens if the crontab is still exactly what the change was planned
        from (and, when given, what the user previewed): a stale plan never overwrites
        someone else's edit.
        """
        with file_lock(self.lock_path):
            current = self.read()
            if digest(current) != change.base_hash or (base_hash and base_hash != change.base_hash):
                raise ConflictError("the crontab changed since the preview; nothing was written. "
                                    "Ask again to get a fresh preview.")
            snapshot = self._snapshot(current)
            # jobs append to logs/<id>.log; if the folder is missing the shell fails the redirect
            # and the command never runs (found by the daemon test)
            self.log_dir.mkdir(parents=True, exist_ok=True)
            self.backend.write(change.after)
            if self.read() != change.after:
                self.backend.write(current)
                raise CrontabError("verification failed after writing; the backup was restored")
        if change.action != "rollback":
            change.backup_id = snapshot
        change.applied = True
        return change

    def _snapshot(self, text: str) -> str:
        self.backup_dir.mkdir(parents=True, exist_ok=True)
        backup_id = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
        (self.backup_dir / f"{backup_id}.cron").write_text(text, encoding="utf-8")
        for old in self.list_backups()[:-MAX_BACKUPS]:
            (self.backup_dir / f"{old}.cron").unlink(missing_ok=True)
        return backup_id

    def list_backups(self) -> list[str]:
        if not self.backup_dir.exists():
            return []
        return sorted(p.stem for p in self.backup_dir.glob("*.cron"))

    def read_log(self, job_id: str, lines: int = 20) -> str:
        if not ID_RE.match(job_id):
            raise ValueError("job_id must be 4-16 lowercase hex characters")
        path = self.log_path(job_id)
        if not path.exists():
            return ""
        return "\n".join(path.read_text(encoding="utf-8", errors="replace").splitlines()[-lines:])
