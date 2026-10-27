"""Crontab storage backends, the single command wrapper, and a cross-platform file lock."""

from __future__ import annotations

import os
import subprocess
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator, Protocol


class CrontabError(RuntimeError):
    """A crontab read or write failed."""


def run(cmd: list[str], stdin: str | None = None, timeout: float = 10) -> str:
    """Run one system command: argument list (never a shell), captured output, a timeout."""
    try:
        proc = subprocess.run(cmd, input=stdin, text=True, capture_output=True, timeout=timeout)
    except FileNotFoundError as exc:
        raise CrontabError(f"{cmd[0]} is not installed") from exc
    except subprocess.TimeoutExpired as exc:
        raise CrontabError(f"{cmd[0]} timed out after {timeout:g}s") from exc
    if proc.returncode != 0:
        raise CrontabError(proc.stderr.strip() or f"{cmd[0]} exited with {proc.returncode}")
    return proc.stdout


class Backend(Protocol):
    name: str

    def read(self) -> str: ...

    def write(self, text: str) -> None: ...


class SystemCrontab:
    """The real per-user crontab, through the privileged crontab(1) binary."""

    name = "system"

    def read(self) -> str:
        try:
            proc = subprocess.run(["crontab", "-l"], text=True, capture_output=True, timeout=10)
        except FileNotFoundError as exc:
            raise CrontabError("crontab is not installed") from exc
        if proc.returncode != 0:
            if "no crontab" in proc.stderr.lower():
                return ""
            raise CrontabError(proc.stderr.strip())
        return proc.stdout

    def write(self, text: str) -> None:
        # Equivalent of `... | crontab -`: the new table goes to crontab's stdin.
        run(["crontab", "-"], stdin=text)


class FileCrontab:
    """A plain text file standing in for the crontab (tests, and demos on Windows)."""

    name = "file"

    def __init__(self, path: Path):
        self.path = path

    def read(self) -> str:
        return _retry(lambda: self.path.read_text(encoding="utf-8") if self.path.exists() else "")

    def write(self, text: str) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(text, encoding="utf-8")
        _retry(lambda: os.replace(tmp, self.path))


def _retry(fn, attempts: int = 100):
    """Windows refuses to replace a file another handle is reading; wait a moment and retry."""
    for _ in range(attempts - 1):
        try:
            return fn()
        except PermissionError:
            time.sleep(0.01)
    return fn()


@contextmanager
def file_lock(path: Path) -> Iterator[None]:
    """Exclusive lock so two writers can't interleave and lose a job (flock on Linux)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a+") as fh:
        if os.name == "posix":
            import fcntl

            fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
        else:
            import msvcrt

            fh.seek(0)
            msvcrt.locking(fh.fileno(), msvcrt.LK_LOCK, 1)
            try:
                yield
            finally:
                fh.seek(0)
                msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)
