"""cron-agent daemon: a small cron daemon for machines without one (Windows) and for demos.

It does what crond does, in user space: wake once a minute, re-read the crontab,
find every line whose schedule matches this minute, and start each command in its
own child process (fork + exec through a shell). Output that a line doesn't
redirect goes to the daemon log. On Linux the system cron is the scheduler; this
daemon only drives the agent's file crontab (CRON_AGENT_BACKEND=file).
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

from croniter import croniter

from . import config
from .cronexpr import MACROS
from .jobs import TAG_RE, JobManager

HEARTBEAT_STALE_S = 90
GIT_BASH = [Path(r"C:\Program Files\Git\bin\bash.exe"), Path(r"C:\Program Files (x86)\Git\bin\bash.exe")]


@dataclass
class Entry:
    schedule: str
    command: str
    stdin: str | None
    job_id: str | None
    line: str


def find_shell() -> list[str]:
    """The shell cron uses: /bin/sh on POSIX; Git Bash on Windows (never the WSL stub)."""
    if os.name == "posix":
        return ["/bin/sh", "-c"]
    for path in GIT_BASH:
        if path.exists():
            return [str(path), "-c"]
    found = shutil.which("bash")
    if found and "system32" not in found.lower():
        return [found, "-c"]
    raise SystemExit("The daemon needs bash on Windows: install Git for Windows (git-scm.com).")


def split_percent(command: str) -> tuple[str, str | None]:
    """crontab(5): an unescaped % ends the command; the rest, % -> newline, becomes stdin."""
    parts = re.split(r"(?<!\\)%", command)
    cmd = parts[0].replace("\\%", "%")
    if len(parts) == 1:
        return cmd, None
    return cmd, "\n".join(p.replace("\\%", "%") for p in parts[1:]) + "\n"


def parse_crontab(text: str) -> list[Entry]:
    entries = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or re.match(r"^[A-Za-z_]\w*\s*=", stripped):
            continue
        if stripped.startswith("@"):
            macro, _, rest = stripped.partition(" ")
            schedule = "@reboot" if macro == "@reboot" else MACROS.get(macro.lower())
            if not schedule:
                continue
        else:
            fields = stripped.split(None, 5)
            if len(fields) < 6:
                continue
            schedule, rest = " ".join(fields[:5]), fields[5]
        tag = TAG_RE.search(rest)
        command, stdin = split_percent(rest)
        entries.append(Entry(schedule, command, stdin, tag.group(1) if tag else None, line))
    return entries


def due(entries: list[Entry], minute: datetime) -> list[Entry]:
    out = []
    for e in entries:
        if e.schedule == "@reboot":
            continue
        try:
            if croniter.match(e.schedule, minute):
                out.append(e)
        except Exception:
            continue  # an unparseable line is skipped, as cron does
    return out


class Daemon:
    def __init__(self, manager: JobManager, log=print):
        self.manager = manager
        self.home = manager.home
        self.state_path = self.home / "daemon.json"
        self.log_path = self.home / "daemon.log"
        self.shell = find_shell()
        self.children: list[tuple[subprocess.Popen, Entry]] = []
        self.started = datetime.now()
        self.runs = 0
        self.say = log

    # ---- bookkeeping

    def _log(self, message: str) -> None:
        stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self.home.mkdir(parents=True, exist_ok=True)
        with open(self.log_path, "a", encoding="utf-8") as fh:
            fh.write(f"{stamp} {message}\n")
        self.say(f"{stamp} {message}")

    def heartbeat(self) -> None:
        self.home.mkdir(parents=True, exist_ok=True)
        state = {"pid": os.getpid(), "started": self.started.isoformat(timespec="seconds"),
                 "heartbeat": datetime.now().isoformat(timespec="seconds"), "runs": self.runs}
        self.state_path.write_text(json.dumps(state), encoding="utf-8")

    def reap(self) -> None:
        for proc, entry in list(self.children):
            code = proc.poll()
            if code is not None:
                self.children.remove((proc, entry))
                self._log(f"job {entry.job_id or '-'} finished, exit code {code}")

    # ---- running jobs

    def spawn(self, entry: Entry) -> subprocess.Popen:
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        with open(self.log_path, "a", encoding="utf-8") as out:
            proc = subprocess.Popen(self.shell + [entry.command], cwd=str(Path.home()),
                                    stdin=subprocess.PIPE if entry.stdin else subprocess.DEVNULL,
                                    stdout=out, stderr=subprocess.STDOUT, creationflags=flags)
        if entry.stdin:
            proc.stdin.write(entry.stdin.encode())
            proc.stdin.close()
        self.children.append((proc, entry))
        self.runs += 1
        self._log(f"job {entry.job_id or '-'} started (pid {proc.pid}): {entry.schedule}")
        return proc

    def tick(self, minute: datetime) -> list[subprocess.Popen]:
        entries = parse_crontab(self.manager.read())
        return [self.spawn(e) for e in due(entries, minute)]

    def run(self, once: bool = False) -> None:
        self._log(f"cron-agent daemon started (pid {os.getpid()}), shell {self.shell[0]}, "
                  f"crontab {getattr(self.manager.backend, 'path', self.manager.backend.name)}")
        for e in parse_crontab(self.manager.read()):
            if e.schedule == "@reboot":
                self.spawn(e)
        last = datetime.now().replace(second=0, microsecond=0) - timedelta(minutes=1)
        try:
            while True:
                now = datetime.now().replace(second=0, microsecond=0)
                minute = last + timedelta(minutes=1)
                while minute <= now:          # catch up if the machine slept briefly
                    if now - minute < timedelta(minutes=5):
                        self.tick(minute)
                    minute += timedelta(minutes=1)
                last = now
                self.reap()
                self.heartbeat()
                if once:
                    for proc, _ in self.children:
                        proc.wait(timeout=60)
                    self.reap()
                    return
                nxt = now + timedelta(minutes=1)
                while datetime.now() < nxt:
                    time.sleep(min(5.0, max(0.2, (nxt - datetime.now()).total_seconds())))
                    self.reap()
                    self.heartbeat()
        except KeyboardInterrupt:
            self._log("cron-agent daemon stopped")
        finally:
            try:
                self.state_path.unlink()
            except OSError:
                pass


def status(home: Path) -> dict:
    """Is a cron-agent daemon alive for this home? Judged by its heartbeat file."""
    path = home / "daemon.json"
    if not path.exists():
        return {"running": False}
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
        age = (datetime.now() - datetime.fromisoformat(state["heartbeat"])).total_seconds()
    except (ValueError, KeyError, OSError):
        return {"running": False}
    return {"running": age < HEARTBEAT_STALE_S, **state}


def main(once: bool = False, force: bool = False) -> None:
    manager = JobManager.from_env()
    if manager.backend.name == "system" and not force:
        sys.exit("This machine has a real crontab, so the system cron daemon runs your jobs.\n"
                 "The cron-agent daemon is for the file backend: run it with --backend file.")
    current = status(manager.home)
    if current.get("running") and not once:
        sys.exit(f"A cron-agent daemon is already running (pid {current['pid']}).")
    Daemon(manager).run(once=once)
