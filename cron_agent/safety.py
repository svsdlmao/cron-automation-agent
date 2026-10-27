"""Command sanitization and the regex blocklist of destructive commands.

The blocklist is a safety net, not the main defence: every write still needs the
user's approval. It exists so that an obviously destructive command is refused
even if a user approves it by mistake.
"""

from __future__ import annotations

import re

BLOCKLIST: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"\brm\s+(-[a-z]*\s+)*-[a-z]*[rf][a-z]*\s+(--no-preserve-root\s+)?/\*?(\s|;|$)", re.I),
     "deletes the root filesystem"),
    (re.compile(r"\bmkfs(\.\w+)?\b"), "formats a disk"),
    (re.compile(r"\bdd\b.*\bof=/dev/"), "writes directly to a disk device"),
    (re.compile(r">\s*/dev/(sd|nvme|hd|vd)[a-z0-9]*"), "overwrites a disk device"),
    (re.compile(r"\b(curl|wget)\b.*\|\s*(sudo\s+)?(ba|z|da|k)?sh\b"), "pipes a download into a shell"),
    (re.compile(r":\(\)\s*\{.*\};\s*:"), "is a fork bomb"),
    (re.compile(r"\bsudo\b|\bsu\s+-?\w*"), "asks for root (sudo/su)"),
    (re.compile(r"/etc/crontab|/etc/cron\.|/var/spool/cron"), "edits system cron files"),
    (re.compile(r"\bcrontab\b[^|;&]*\s-[a-z]*r[a-z]*\b"), "deletes the crontab"),
    (re.compile(r"\bchmod\s+(-\w+\s+)*-R\s+0?777\s+/(\s|$)"), "opens permissions on /"),
    (re.compile(r"\b(shutdown|reboot|halt|poweroff)\b"), "powers off the machine"),
    (re.compile(r"\bchown\s+(-\w+\s+)*-R\s+\S+\s+/(\s|$)"), "changes ownership of /"),
]

CONTROL_CHARS = re.compile(r"[\x00-\x08\x0a-\x1f\x7f]")
TAG_MARKER = "cron-agent:"
MAX_COMMAND_LENGTH = 500


class UnsafeCommand(ValueError):
    """The command was refused before reaching the crontab."""


def check_command(command: str) -> str:
    """Return the stripped command, or raise UnsafeCommand explaining why it was refused."""
    command = command.strip()
    if not command:
        raise UnsafeCommand("the command is empty")
    if len(command) > MAX_COMMAND_LENGTH:
        raise UnsafeCommand(f"the command is longer than {MAX_COMMAND_LENGTH} characters")
    if CONTROL_CHARS.search(command):
        raise UnsafeCommand("the command contains a newline or control character, which could "
                            "smuggle a second line into the crontab")
    if TAG_MARKER in command:
        raise UnsafeCommand("the command may not contain the agent's own tag")
    for pattern, reason in BLOCKLIST:
        if pattern.search(command):
            raise UnsafeCommand(f"blocked: this command {reason}")
    return command


def blocked_reason(command: str) -> str | None:
    try:
        check_command(command)
    except UnsafeCommand as exc:
        return str(exc)
    return None


def escape_percent(command: str) -> str:
    """In a crontab an unescaped % ends the command, so every bare % becomes \\%."""
    return re.sub(r"(?<!\\)%", r"\\%", command)


def unescape_percent(command: str) -> str:
    return command.replace("\\%", "%")
