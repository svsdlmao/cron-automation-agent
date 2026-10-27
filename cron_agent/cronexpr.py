"""Strict parsing, validation, preview and English read-back of 5-field cron expressions."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime

FIELDS = (
    ("minute", 0, 59),
    ("hour", 0, 23),
    ("day_of_month", 1, 31),
    ("month", 1, 12),
    ("day_of_week", 0, 7),
)

MONTH_NAMES = {m: i for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], start=1)}
DAY_NAMES = {d: i for i, d in enumerate(["sun", "mon", "tue", "wed", "thu", "fri", "sat"])}

MACROS = {
    "@hourly": "0 * * * *",
    "@daily": "0 0 * * *",
    "@midnight": "0 0 * * *",
    "@weekly": "0 0 * * 0",
    "@monthly": "0 0 1 * *",
    "@yearly": "0 0 1 1 *",
    "@annually": "0 0 1 1 *",
}

# One comma-separated item: "*", "5", "1-5", "*/15", "1-10/2", "mon", "jan-mar".
ITEM_RE = re.compile(r"^(\*|[0-9a-z]+(?:-[0-9a-z]+)?)(?:/([0-9]+))?$", re.IGNORECASE)

# A whole expression inside free text, used by the translator to spot literal cron.
EXPR_IN_TEXT_RE = re.compile(
    r"(?<![\w*/,-])((?:[\d*][\d*/,-]*\s+){4}[\d*a-z][\w*/,-]*)(?![\w*/,-])", re.IGNORECASE)


class CronError(ValueError):
    """Raised for an expression that cron would reject or misread."""


def _value(token: str, name: str, lo: int, hi: int) -> int:
    token = token.lower()
    if token.isdigit():
        value = int(token)
    elif name == "month" and token in MONTH_NAMES:
        value = MONTH_NAMES[token]
    elif name == "day_of_week" and token in DAY_NAMES:
        value = DAY_NAMES[token]
    else:
        raise CronError(f"{name}: {token!r} is not a number" +
                        (" or a name" if name in ("month", "day_of_week") else ""))
    if not lo <= value <= hi:
        raise CronError(f"{name}: {value} is outside {lo}-{hi}")
    return value


def validate_field(name: str, text: str, lo: int, hi: int) -> None:
    if not text:
        raise CronError(f"{name} is empty")
    for item in text.split(","):
        m = ITEM_RE.match(item)
        if not m:
            raise CronError(f"{name}: {item!r} is not valid cron syntax")
        base, step = m.group(1), m.group(2)
        if step is not None:
            if int(step) < 1 or int(step) > hi:
                raise CronError(f"{name}: step /{step} must be between 1 and {hi}")
            if base != "*" and "-" not in base:
                raise CronError(f"{name}: a step needs * or a range before it, e.g. */{step}")
        if base == "*":
            continue
        if "-" in base:
            a, b = base.split("-", 1)
            start, end = _value(a, name, lo, hi), _value(b, name, lo, hi)
            if start > end:
                raise CronError(f"{name}: range {base} runs backwards")
        else:
            _value(base, name, lo, hi)


@dataclass(frozen=True)
class CronExpr:
    minute: str
    hour: str
    day_of_month: str
    month: str
    day_of_week: str

    @classmethod
    def parse(cls, text: str) -> "CronExpr":
        text = " ".join(text.strip().split())
        if text.startswith("@"):
            if text.lower() not in MACROS:
                raise CronError(f"unsupported macro {text!r}")
            text = MACROS[text.lower()]
        parts = text.split(" ")
        if len(parts) != 5:
            raise CronError(f"expected 5 fields (minute hour day month weekday), got {len(parts)}")
        for (name, lo, hi), value in zip(FIELDS, parts):
            validate_field(name, value, lo, hi)
        return cls(*parts)

    def __str__(self) -> str:
        return " ".join((self.minute, self.hour, self.day_of_month, self.month, self.day_of_week))

    @property
    def both_day_fields_restricted(self) -> bool:
        """Cron ORs day-of-month and day-of-week when both are set — a classic trap."""
        return self.day_of_month != "*" and self.day_of_week != "*"

    def next_runs(self, count: int = 5, start: datetime | None = None) -> list[datetime]:
        from croniter import croniter

        it = croniter(str(self), start or datetime.now())
        return [it.get_next(datetime) for _ in range(count)]

    def describe(self) -> str:
        try:
            from cron_descriptor import ExpressionDescriptor, Options

            options = Options()
            options.use_24hour_time_format = True
            options.verbose = False
            return ExpressionDescriptor(str(self), options).get_description()
        except Exception:  # descriptor is a nicety; never block on it
            return str(self)

    def warnings(self) -> list[str]:
        notes = []
        if self.both_day_fields_restricted:
            notes.append("Both day-of-month and day-of-week are set: cron runs the job when "
                         "EITHER matches, not both.")
        if self.minute == "*":
            notes.append("The minute field is *, so this runs every minute in the matching hours.")
        m = re.fullmatch(r"\*/(\d+)", self.minute)
        if m and 60 % int(m.group(1)):
            notes.append(f"*/{m.group(1)} restarts every hour, so the gap before :00 is shorter.")
        return notes


def equivalent(a: str, b: str, samples: int = 40, start: datetime | None = None) -> bool:
    """True when two expressions fire at the same times (compares the next `samples` runs)."""
    from croniter import croniter

    start = start or datetime(2026, 1, 1)
    ia, ib = croniter(a, start), croniter(b, start)
    return all(ia.get_next(datetime) == ib.get_next(datetime) for _ in range(samples))
