"""Rule-based planner: plain English -> exactly one tool call. Works offline, no API key.

It is deliberately conservative: when a request is ambiguous (no time given, no
command named) it asks instead of guessing, and every assumption it makes is
returned as a note that the user sees before approving anything.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from .cronexpr import EXPR_IN_TEXT_RE, CronError, CronExpr

WEEKDAYS = {
    "sunday": 0, "sun": 0, "monday": 1, "mon": 1, "tuesday": 2, "tue": 2, "tues": 2,
    "wednesday": 3, "wed": 3, "thursday": 4, "thu": 4, "thur": 4, "thurs": 4,
    "friday": 5, "fri": 5, "saturday": 6, "sat": 6,
}
MONTHS = {
    "january": 1, "jan": 1, "february": 2, "feb": 2, "march": 3, "mar": 3, "april": 4,
    "apr": 4, "may": 5, "june": 6, "jun": 6, "july": 7, "jul": 7, "august": 8, "aug": 8,
    "september": 9, "sep": 9, "sept": 9, "october": 10, "oct": 10, "november": 11,
    "nov": 11, "december": 12, "dec": 12,
}
NUMBER_WORDS = {
    "a": 1, "an": 1, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6,
    "seven": 7, "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12,
    "fifteen": 15, "twenty": 20, "thirty": 30, "forty-five": 45, "ninety": 90,
}
ORDINALS = {"first": 1, "1st": 1, "second": 2, "2nd": 2, "third": 3, "3rd": 3,
            "fourth": 4, "4th": 4, "last": -1}

DAY_RE = "|".join(sorted(WEEKDAYS, key=len, reverse=True))
MONTH_RE = "|".join(sorted(MONTHS, key=len, reverse=True))
NUM_RE = r"\d+|" + "|".join(sorted(NUMBER_WORDS, key=len, reverse=True))

RECURRING_RE = re.compile(
    r"\b(every|each|daily|weekly|monthly|hourly|yearly|annually|nightly|weekdays?|weekends?|"
    r"fortnightly|biweekly|(?:mon|tues|wednes|thurs|fri|satur|sun)days)\b")
ONE_OFF_RE = re.compile(
    r"\b(in \d+ (?:minutes?|mins?|hours?|days?)|once|tomorrow|today|tonight|next (?:week|month|"
    r"monday|tuesday|wednesday|thursday|friday|saturday|sunday))\b")

GUARD_WEEKDAY = '[ "$(date +%u)" = {u} ] && '
GUARD_LAST_WEEKDAY = "[ \"$(date -d '+7 days' +%d)\" -le 7 ] && "
GUARD_LAST_DAY = '[ "$(date -d tomorrow +%d)" = 01 ] && '

FILLER = {"my", "the", "a", "an", "please", "script", "scripts", "job", "task", "program",
          "file", "it", "to", "can", "you", "could", "would", "i", "want", "need", "and",
          "then", "schedule", "set", "up", "make", "sure", "that", "this", "for", "me", "of"}


@dataclass
class Plan:
    kind: str                       # "tool", "ask" or "reply"
    tool: str | None = None
    args: dict = field(default_factory=dict)
    message: str = ""
    notes: list[str] = field(default_factory=list)
    pending: str | None = None      # what an "ask" is waiting for: time, command, frequency
    meta: dict = field(default_factory=dict)   # planner diagnostics (attempts, recovery path)


class NeedInfo(Exception):
    def __init__(self, question: str, pending: str):
        super().__init__(question)
        self.question, self.pending = question, pending


def _num(token: str) -> int:
    return int(token) if token.isdigit() else NUMBER_WORDS[token]


def normalise(text: str) -> str:
    t = text.lower().replace("’", "'").replace("a.m.", "am").replace("p.m.", "pm")
    t = re.sub(r"\bo'?clock\b", "", t)
    t = re.sub(r"(\d)\s*(am|pm)\b", r"\1\2", t)
    return re.sub(r"\s+", " ", t).strip()


# ---------------------------------------------------------------- commands

QUOTED_RE = re.compile(r"`([^`]+)`|\"([^\"]+)\"|(?:^|(?<=\s))'([^']+)'(?=\s|$|[,.;])")
PATH_RE = re.compile(r"(?<![\w/*])((?:~|\.{1,2})?/[A-Za-z_.~][^\s,;]*)")


def extract_command(text: str) -> tuple[str | None, str]:
    """Return (command, text with the command removed). Quotes win, then a path."""
    for m in QUOTED_RE.finditer(text):
        candidate = next(g for g in m.groups() if g)
        try:  # a quoted cron expression is a schedule, not a command
            CronExpr.parse(candidate)
            continue
        except CronError:
            pass
        return candidate.strip(), text[: m.start()] + " " + text[m.end():]
    m = PATH_RE.search(text)
    if m:
        end = m.end()
        rest = text[end:]
        args = re.match(r"((?:\s+(?:-{1,2}[\w=-]+|[\w./~-]*/[\w./~-]*))*)", rest)
        if args:
            end += len(args.group(1))
        return text[m.start():end].strip(), text[: m.start()] + " " + text[end:]
    return None, text


def command_query(leftover: str) -> str | None:
    """Words left after the schedule is removed, e.g. 'database backup'."""
    t = normalise(leftover)
    t = re.sub(r"\b(run|execute|start|launch|trigger|call|do|kick off)\b", " ", t)
    t = t.replace("back up", "backup")
    words = [w.strip("._-") for w in re.split(r"[^a-z0-9_.-]+", t)]
    words = [w for w in words if w and w not in FILLER]
    return " ".join(words) or None


# ---------------------------------------------------------------- times

@dataclass
class TimeSpec:
    times: list[tuple[int, int]] = field(default_factory=list)
    hour_range: tuple[int, int] | None = None
    notes: list[str] = field(default_factory=list)


def _to_24h(hour: int, minute: int, meridiem: str | None) -> tuple[int, int]:
    if meridiem == "am":
        hour = 0 if hour == 12 else hour
    elif meridiem == "pm":
        hour = 12 if hour == 12 else hour + 12
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        raise NeedInfo(f"{hour}:{minute:02d} isn't a valid time. What time did you mean?", "time")
    return hour, minute


CLOCK_RE = re.compile(r"\b(\d{1,2})(?::(\d{2}))?(am|pm)\b|\b(\d{1,2}):(\d{2})\b")


def parse_times(t: str) -> tuple[TimeSpec, str]:
    spec = TimeSpec()

    # Hour windows first, so their times aren't read as start times.
    if re.search(r"\b(working|business|office) hours\b", t):
        spec.hour_range = (9, 16)
        t = re.sub(r"\b(during |in )?(working|business|office) hours\b", " ", t)
    m = re.search(r"\b(?:between|from) (\d{1,2})(?::(\d{2}))?(am|pm)? (?:and|to|-|until|till) "
                  r"(\d{1,2})(?::(\d{2}))?(am|pm)?\b", t)
    if m:
        end_mer = m.group(6)
        start_mer = m.group(3) or (end_mer if end_mer == "am" or int(m.group(1)) < int(m.group(4))
                                   else "am")
        start, _ = _to_24h(int(m.group(1)), 0, start_mer)
        end, end_min = _to_24h(int(m.group(4)), int(m.group(5) or 0), end_mer)
        last = end if end_min else end - 1
        if last < start:
            raise NeedInfo("That time window wraps past midnight. Which hours exactly?", "time")
        spec.hour_range = (start, last)
        t = t[: m.start()] + " " + t[m.end():]

    for word, value in (("midnight", (0, 0)), ("noon", (12, 0)), ("midday", (12, 0))):
        if re.search(rf"\b{word}\b", t):
            spec.times.append(value)
            t = re.sub(rf"\b(at )?{word}\b", " ", t)

    for m in re.finditer(r"\b(half past|quarter past|quarter to) (\d{1,2})(am|pm)?\b", t):
        h = int(m.group(2))
        minute = {"half past": 30, "quarter past": 15, "quarter to": 45}[m.group(1)]
        if m.group(1) == "quarter to":
            h -= 1
        spec.times.append(_to_24h(h, minute, m.group(3)))
    t = re.sub(r"\b(at )?(half past|quarter past|quarter to) \d{1,2}(am|pm)?\b", " ", t)

    for m in CLOCK_RE.finditer(t):
        if m.group(1):
            spec.times.append(_to_24h(int(m.group(1)), int(m.group(2) or 0), m.group(3)))
        else:
            spec.times.append(_to_24h(int(m.group(4)), int(m.group(5)), None))
    t = CLOCK_RE.sub(" ", t)

    for m in re.finditer(r"\bat (\d{1,2})\b(?! ?(?:minutes?|mins?|hours?|st|nd|rd|th|%))", t):
        hour = int(m.group(1))
        if hour > 23:
            raise NeedInfo(f"{hour} isn't an hour of the day. What time did you mean?", "time")
        spec.times.append((hour, 0))
        if 1 <= hour <= 7:
            spec.notes.append(f"Read “at {hour}” as {hour:02d}:00 (24-hour clock). "
                              f"Say “{hour}pm” if you meant the afternoon.")
    t = re.sub(r"\bat (\d{1,2})\b(?! ?(?:minutes?|mins?|hours?|st|nd|rd|th))", " ", t)

    if not spec.times:
        for word, value in (("morning", (9, 0)), ("evening", (18, 0)), ("afternoon", (14, 0)),
                            ("night", (22, 0)), ("nightly", (22, 0)), ("tonight", (22, 0))):
            if re.search(rf"\b{word}\b", t):
                spec.times.append(value)
                spec.notes.append(f"Assumed {value[0]:02d}:00 for “{word}”. "
                                  "Give a time to change it.")
                break
    t = re.sub(r"\bat\b", " ", t)
    return spec, t


# ---------------------------------------------------------------- schedules

@dataclass
class Schedule:
    exprs: list[str]
    guard: str = ""
    notes: list[str] = field(default_factory=list)


def parse_schedule(text: str) -> tuple[Schedule, str]:
    """Turn the schedule part of a request into cron. Returns (schedule, unconsumed text)."""
    t = normalise(text)
    notes: list[str] = []

    literal = EXPR_IN_TEXT_RE.search(t)
    if literal:
        try:
            expr = CronExpr.parse(literal.group(1))
            return Schedule([str(expr)]), t[: literal.start()] + " " + t[literal.end():]
        except CronError:
            pass

    if re.search(r"\bevery (?:\d+ |few )?seconds?\b", t):
        raise NeedInfo("Cron can’t run jobs more often than once a minute. Is every minute OK?",
                       "clarify")
    if re.search(r"\b(every other week|every (two|2) weeks|fortnightly|biweekly)\b", t):
        raise NeedInfo("Cron has no “every two weeks”. Should it run weekly instead, or on the "
                       "1st and 15th of each month?", "clarify")
    if not RECURRING_RE.search(t) and ONE_OFF_RE.search(t):
        raise OneOff()

    times, t = parse_times(t)
    notes += times.notes
    minute, hour, dom, month, dow = None, None, "*", "*", "*"
    guard = ""

    # --- intervals
    m = re.search(rf"\bevery ({NUM_RE})? ?(?:minutes?|mins?)\b", t)
    if m or re.search(r"\bevery half(?: an)? hour\b", t):
        n = _num(m.group(1)) if m and m.group(1) else (30 if not m else 1)
        t = re.sub(rf"\bevery ({NUM_RE})? ?(?:minutes?|mins?)\b|\bevery half(?: an)? hour\b", " ", t)
        if n >= 60 and n % 60 == 0:
            minute, hour = "0", f"*/{n // 60}" if n > 60 else "*"
        elif n == 90:
            exprs = ["0 0-21/3 * * *", "30 1-22/3 * * *"]
            notes.append("Cron steps restart every hour, so every 90 minutes needs two lines "
                         "(00:00, 03:00 … and 01:30, 04:30 …).")
            return Schedule(exprs, notes=notes), t
        elif n >= 60:
            raise NeedInfo(f"Every {n} minutes doesn’t line up with hours, so cron can’t do it "
                           "in one line. Would every hour or every two hours work?", "clarify")
        else:
            minute = "*" if n == 1 else f"*/{n}"
            if 60 % n:
                notes.append(f"*/{n} restarts at the top of each hour, so the last gap is shorter.")
        if times.hour_range:
            hour = f"{times.hour_range[0]}-{times.hour_range[1]}"
        hour = hour or "*"

    m = re.search(rf"\bevery ({NUM_RE}) ?(?:hours?|hrs?)\b", t)
    if minute is None and m:
        n = _num(m.group(1))
        if not 1 <= n <= 23:
            raise NeedInfo("How many hours apart should it run (1–23)?", "clarify")
        minute = str(times.times[0][1]) if times.times else "0"
        base = f"{times.hour_range[0]}-{times.hour_range[1]}" if times.hour_range else "*"
        hour = base if n == 1 else f"{base}/{n}"
        t = t[: m.start()] + " " + t[m.end():]

    if minute is None and re.search(r"\b(every hour|hourly|each hour)\b", t):
        past = re.search(r"\b(\d{1,2}) (?:minutes? )?past\b|\bat :(\d{2})\b", t)
        minute = (past.group(1) or past.group(2)) if past else "0"
        hour = f"{times.hour_range[0]}-{times.hour_range[1]}" if times.hour_range else "*"
        t = re.sub(r"\b(every hour|hourly|each hour)\b", " ", t)

    # --- days
    nth = re.search(rf"\b(first|second|third|fourth|last|1st|2nd|3rd|4th) ({DAY_RE})s?\b"
                    r"(?: (?:of|in) (?:the|every|each) month\b)?", t)
    if nth:
        k, day = ORDINALS[nth.group(1)], WEEKDAYS[nth.group(2)]
        if k == -1:
            dow = str(day)
            guard = GUARD_LAST_WEEKDAY
            notes.append("Cron can’t say “last weekday of the month”, so the job runs on that "
                         "weekday and a date check skips all but the last one.")
        else:
            dom = f"{(k - 1) * 7 + 1}-{k * 7}"
            guard = GUARD_WEEKDAY.format(u=7 if day == 0 else day)
            notes.append("Cron ORs the two day fields, so it runs on days "
                         f"{dom} and a date check keeps only the {nth.group(2).title()}.")
        t = t[: nth.start()] + " " + t[nth.end():]
    elif re.search(r"\blast day of (?:the|every|each) month\b", t):
        dom, guard = "28-31", GUARD_LAST_DAY
        notes.append("Cron has no “last day of the month”, so it runs on the 28th–31st and a "
                     "date check keeps only the real last day.")
        t = re.sub(r"\blast day of (?:the|every|each) month\b", " ", t)

    if dow == "*" and not guard:
        if re.search(r"\b(every )?weekdays?\b|\b(monday|mon) (to|through|thru|-) (friday|fri)\b|"
                     r"\bworkdays?\b|\bmon-fri\b", t):
            dow = "1-5"
            t = re.sub(r"\b(every |on )?weekdays?\b|\b(monday|mon) (to|through|thru|-) "
                       r"(friday|fri)\b|\b(on )?workdays?\b|\bmon-fri\b", " ", t)
        elif re.search(r"\b(every )?weekends?\b", t):
            dow = "0,6"
            t = re.sub(r"\b(every |on |at )?(the )?weekends?\b", " ", t)
        else:
            days = sorted({WEEKDAYS[d] for d in re.findall(rf"\b({DAY_RE})s?\b", t)})
            if days:
                dow = ",".join(map(str, days))
                t = re.sub(rf"\b({DAY_RE})s?\b", " ", t)

    if dom == "*" and not guard:
        m = re.search(r"\b(?:on )?(?:the )?((?:\d{1,2}(?:st|nd|rd|th)?(?:,? (?:and )?(?:the )?)?)+)"
                      r"(?: day)? of (?:every|each|the) month\b", t) or \
            re.search(r"\bevery month on (?:the )?((?:\d{1,2}(?:st|nd|rd|th)?(?:,? (?:and )?"
                      r"(?:the )?)?)+)", t) or \
            re.search(r"\bmonthly on (?:the )?((?:\d{1,2}(?:st|nd|rd|th)?(?:,? (?:and )?"
                      r"(?:the )?)?)+)", t) or \
            re.search(r"\bon the ((?:\d{1,2}(?:st|nd|rd|th)(?:,? (?:and )?(?:the )?)?)+)", t)
        if m:
            nums = sorted({int(n) for n in re.findall(r"\d{1,2}", m.group(1))})
            if any(not 1 <= n <= 31 for n in nums):
                raise NeedInfo("Days of the month run from 1 to 31. Which day did you mean?",
                               "clarify")
            dom = ",".join(map(str, nums))
            if any(n > 28 for n in nums):
                notes.append("Months without that day are skipped.")
            t = t[: m.start()] + " " + t[m.end():]
        elif re.search(r"\bfirst day of (?:the|every|each) month\b", t):
            dom = "1"
            t = re.sub(r"\bfirst day of (?:the|every|each) month\b", " ", t)

    months = [mo for mo in re.findall(rf"\b({MONTH_RE})\b", t)
              if mo != "may" or re.search(r"\b(in|every|of|on) may\b|\bmay \d|\d(st|nd|rd|th)? "
                                          r"(of )?may\b", t)]
    if months:
        month = ",".join(map(str, sorted({MONTHS[mo] for mo in months})))
        date = re.search(rf"\b(\d{{1,2}})(?:st|nd|rd|th)? (?:of )?(?:{MONTH_RE})\b|"
                         rf"\b(?:{MONTH_RE}) (\d{{1,2}})(?:st|nd|rd|th)?\b", t)
        if date and dom == "*":
            dom = date.group(1) or date.group(2)
        elif dom == "*" and dow == "*":
            dom = "1"
            notes.append("Assumed the 1st of the month. Name a day to change it.")
        t = re.sub(rf"\b\d{{1,2}}(?:st|nd|rd|th)? (?:of )?(?:{MONTH_RE})\b|\b(?:{MONTH_RE})"
                   rf"(?: \d{{1,2}}(?:st|nd|rd|th)?)?\b", " ", t)

    if re.search(r"\b(yearly|annually|every year)\b", t) and month == "*":
        month, dom = "1", dom if dom != "*" else "1"
        notes.append("Assumed 1 January. Name a date to change it.")
    if re.search(r"\b(monthly|every month)\b", t) and dom == "*" and not guard and dow == "*":
        dom = "1"
        notes.append("Assumed the 1st of each month. Name a day to change it.")
    if re.search(r"\b(weekly|every week)\b", t) and dow == "*" and dom == "*":
        dow = "0"
        notes.append("Assumed Sunday. Name a day to change it.")
    m = re.search(rf"\bevery ({NUM_RE}) days\b|\bevery other day\b", t)
    if m:
        n = _num(m.group(1)) if m.group(1) else 2
        dom = f"*/{n}"
        notes.append(f"*/{n} counts from the 1st of each month, so the gap resets each month.")
    m = re.search(rf"\bevery ({NUM_RE}) months\b", t)
    if m:
        month, dom = f"*/{_num(m.group(1))}", dom if dom != "*" else "1"
    t = re.sub(r"\b(every|each|daily|weekly|monthly|yearly|annually|nightly|day|days|week|month|"
               r"months|year|morning|evening|afternoon|night|on|in|of|the)\b", " ", t)

    if minute is None:
        if not times.times:
            if not RECURRING_RE.search(normalise(text)) and dow == "*" and dom == "*":
                raise NeedInfo("How often should it run? For example “every day at 9am”.",
                               "frequency")
            raise NeedInfo("At what time should it run?", "time")
        if not RECURRING_RE.search(normalise(text)) and (dom, month, dow) == ("*", "*", "*"):
            notes.append("No repeat word was given, so this runs every day.")
        by_minute: dict[int, list[int]] = {}
        for h, mi in times.times:
            by_minute.setdefault(mi, []).append(h)
        exprs = []
        for mi, hours in sorted(by_minute.items()):
            exprs.append(f"{mi} {','.join(map(str, sorted(set(hours))))} {dom} {month} {dow}")
    else:
        exprs = [f"{minute} {hour} {dom} {month} {dow}"]

    for e in exprs:
        expr = CronExpr.parse(e)
        if expr.both_day_fields_restricted and not guard:
            notes.append("Both a date and a weekday are set, so cron runs on either one.")
    return Schedule(exprs, guard, notes), t


class OneOff(Exception):
    pass


# ---------------------------------------------------------------- intents

HEX_ID_RE = re.compile(r"\b([0-9a-f]{4})\b")


def _job_ref(text: str) -> dict:
    ids = HEX_ID_RE.findall(normalise(text))
    words = command_query(re.sub(r"\b(remove|delete|stop|cancel|unschedule|disable|drop|job|"
                                 r"log|logs|output|show|did|run|ran|last|for|undo|roll ?back)\b",
                                 " ", normalise(text)))
    return {"ids": ids, "words": words}


def plan(text: str) -> Plan:
    raw = text.strip()
    if not raw:
        return Plan("reply", message="")
    command, rest = extract_command(raw)
    t = normalise(rest)  # intents are read with the command removed: `>> health.log` isn't "log"
    if re.fullmatch(r"(help|\?|what can you do\??|commands)", t):
        return Plan("reply", message=HELP)

    if re.search(r"\b(undo|roll ?back|revert|restore)\b", t):
        return Plan("tool", "rollback", {})
    if re.search(r"\b(remove|delete|stop|cancel|unschedule|disable|drop)\b", t) and \
            not RECURRING_RE.search(t.replace("every job", "")) and not command:
        return Plan("tool", "remove_job", {"ref": _job_ref(raw)})
    if re.search(r"\b(status|is cron (running|working|up|alive|on)|cron daemon|daemon)\b", t):
        return Plan("tool", "cron_status", {})
    if re.search(r"\b(log|logs|output)\b|\bdid .+ (run|work|finish)\b|\blast run\b", t) and \
            not RECURRING_RE.search(t):
        return Plan("tool", "read_job_log", {"ref": _job_ref(raw)})

    literal = EXPR_IN_TEXT_RE.search(normalise(raw))
    if re.search(r"\b(explain|what does|when (does|will|would|is)|preview|mean|means)\b", t) and \
            literal:
        return Plan("tool", "preview_schedule", {"schedule": literal.group(1)})
    if re.search(r"\b(list|show|what'?s|what is|what are|which|my)\b.*\b(jobs?|scheduled|schedules?|"
                 r"crontab|tasks?|running)\b", t) and not RECURRING_RE.search(t):
        return Plan("tool", "list_jobs", {})
    if re.search(r"\b(scripts?)\b", t) and re.search(r"\b(list|show|find|which|what)\b", t) and \
            not RECURRING_RE.search(t):
        return Plan("tool", "find_script", {"query": ""})

    try:
        schedule, leftover = parse_schedule(rest)
    except OneOff:
        return Plan("reply", message=(
            "That sounds like a one-off job. Cron is for repeating jobs; for a single run use "
            "at(1), for example:  echo '<command>' | at now + 10 minutes"))
    except NeedInfo as need:
        return Plan("ask", message=need.question, pending=need.pending)

    args: dict = {"schedules": schedule.exprs, "guard": schedule.guard}
    if command:
        args["command"] = command
    else:
        query = command_query(leftover)
        if not query:
            return Plan("ask", message="What command or script should it run?", pending="command",
                        notes=schedule.notes, args=args)
        args["command_query"] = query
    return Plan("tool", "add_job", args, notes=schedule.notes)


HELP = """Things you can say:
  run ~/scripts/db_backup.sh every Friday at midnight
  every 15 minutes during working hours on weekdays run `~/bin/health_check.sh`
  back up my database on the first Monday of the month at 9am
  what's scheduled?          is cron running?          did the backup run?
  explain */15 9-16 * * 1-5  remove job a1f3            undo
Type exit to quit."""
