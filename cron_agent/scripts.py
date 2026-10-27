"""Context-aware script lookup: map “my database backup script” to ~/scripts/db_backup.sh."""

from __future__ import annotations

import re
from difflib import SequenceMatcher
from pathlib import Path

SYNONYMS = {
    "database": "db", "databases": "db", "postgres": "db", "mysql": "db", "sql": "db",
    "backups": "backup", "bak": "backup", "reports": "report", "clean": "cleanup",
    "clear": "cleanup", "cleaning": "cleanup", "logs": "log", "checks": "check",
    "healthcheck": "health", "syncing": "sync", "synchronise": "sync", "synchronize": "sync",
    "temp": "tmp", "temporary": "tmp", "weekly": "week", "daily": "day",
}
IGNORED = {"sh", "py", "bash", "pl", "rb", "my", "the", "a", "script", "job", "run", "file"}


def tokens(text: str) -> set[str]:
    text = text.lower().replace("back up", "backup")
    out = set()
    for word in re.split(r"[^a-z0-9]+", text):
        if not word or word in IGNORED:
            continue
        out.add(SYNONYMS.get(word, word))
    return out


def _display(path: Path) -> str:
    try:
        return "~/" + path.relative_to(Path.home()).as_posix()
    except ValueError:
        return path.as_posix()


def find_scripts(query: str, dirs: list[Path], limit: int = 10) -> list[dict]:
    files = [p for d in dirs if d.is_dir() for p in sorted(d.iterdir())
             if p.is_file() and not p.name.startswith(".")]
    if not query.strip():
        return [{"path": p.as_posix(), "display": _display(p), "score": 1.0} for p in files[:50]]
    wanted = tokens(query)
    if not wanted:
        return []
    scored = []
    for p in files:
        have = tokens(p.stem)
        hits = sum(1.0 if w in have else 0.5 if any(h.startswith(w) or w.startswith(h)
                                                      for h in have if len(h) > 2) else 0.0
                   for w in wanted)
        score = hits / len(wanted)
        score += 0.1 * SequenceMatcher(None, " ".join(sorted(wanted)), " ".join(sorted(have))).ratio()
        if score >= 0.5:
            scored.append({"path": p.as_posix(), "display": _display(p), "score": round(score, 2)})
    scored.sort(key=lambda r: -r["score"])
    return scored[:limit]
