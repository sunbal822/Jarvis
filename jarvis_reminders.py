"""
Jarvis reminders: storage + plain-English time parsing.

Time words are parsed with plain code (no AI), so "tomorrow at 3pm" always means exactly that.
Reminders live in ~/jarvis_output/reminders.json. The Jarvis window checks that file every
few seconds and pops up / speaks a reminder when it is due.
"""

import json
import os
import re
import uuid
from datetime import datetime, timedelta
from pathlib import Path

OUT = Path.home() / "jarvis_output"
FILE = OUT / "reminders.json"

_WEEKDAYS = {"monday": 0, "mon": 0, "tuesday": 1, "tue": 1, "tues": 1, "wednesday": 2, "wed": 2,
             "thursday": 3, "thu": 3, "thur": 3, "thurs": 3, "friday": 4, "fri": 4,
             "saturday": 5, "sat": 5, "sunday": 6, "sun": 6}
_MONTHS = {"january": 1, "jan": 1, "february": 2, "feb": 2, "march": 3, "mar": 3, "april": 4, "apr": 4,
           "may": 5, "june": 6, "jun": 6, "july": 7, "jul": 7, "august": 8, "aug": 8,
           "september": 9, "sept": 9, "sep": 9, "october": 10, "oct": 10, "november": 11, "nov": 11,
           "december": 12, "dec": 12}
_NUMWORDS = {"a": 1, "an": 1, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7,
             "eight": 8, "nine": 9, "ten": 10, "fifteen": 15, "twenty": 20, "thirty": 30, "forty five": 45}
_MON_RE = "|".join(sorted(_MONTHS, key=len, reverse=True))
_DAY_RE = "|".join(sorted(_WEEKDAYS, key=len, reverse=True))


_HOURWORDS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8,
              "nine": 9, "ten": 10, "eleven": 11, "twelve": 12}
_MINWORDS = {"fifteen": 15, "twenty": 20, "thirty": 30, "forty five": 45, "forty-five": 45}
_HOUR_RE = "|".join(_HOURWORDS)
_MINWORD_RE = "|".join(sorted(_MINWORDS, key=len, reverse=True))
_UNIT_RE = r"(?:seconds?|secs?|minutes?|mins?|hours?|hrs?|days?|weeks?)"
# abbreviations that are also ordinary words ("he sat down", "the sun", "mon"): only trusted after next/this/on/coming
_AMBIGUOUS_DAYS = {"mon", "wed", "sat", "sun"}


def _spelled_times(t):
    """'at five' -> 'at 5', 'five thirty pm' -> '5:30 pm'. Only in clock contexts, so 'in five minutes' is untouched."""
    def clock(h, mw):
        return f"{_HOURWORDS[h]}:{_MINWORDS[mw]:02d}" if mw else str(_HOURWORDS[h])

    t = re.sub(r"(?<!\w)(at|@|by|around)\s+(" + _HOUR_RE + r")(?:\s+(" + _MINWORD_RE + r"))?\b(?!\s*" + _UNIT_RE + r")",
               lambda m: f"{m.group(1)} {clock(m.group(2), m.group(3))}", t)
    t = re.sub(r"\b(" + _HOUR_RE + r")(?:\s+(" + _MINWORD_RE + r"))?\s*(am|pm|o'?clock)\b",
               lambda m: f"{clock(m.group(1), m.group(2))} {m.group(3)}", t)
    return t


def _guess_hour(h, period_hint):
    """A bare hour with no am/pm: pick the sensible one."""
    if h >= 13 or h == 0:
        return h
    if period_hint in ("pm", "evening", "afternoon", "night"):
        return (h % 12) + 12
    if period_hint == "morning":
        return h % 12
    return (h % 12) + 12 if h <= 6 or h == 12 else h  # 1-6 -> pm, 7-11 -> am, 12 -> noon


def _time_of_day(t, period_hint):
    """Find a clock time in the text. Returns (hour, minute) or None."""
    if re.search(r"\bnoon\b", t):
        return 12, 0
    if re.search(r"\bmidnight\b", t):
        return 0, 0
    m = re.search(r"\b(\d{1,2})(?::(\d{2}))?\s*(am|pm)\b", t)
    if m:
        h, mi, ap = int(m.group(1)), int(m.group(2) or 0), m.group(3)
        if not 1 <= h <= 12 or mi > 59:
            return None
        return (h % 12) + (12 if ap == "pm" else 0), mi
    m = re.search(r"\b([01]?\d|2[0-3]):([0-5]\d)\b", t)  # 17:30
    if m:
        h, mi = int(m.group(1)), int(m.group(2))
        if len(m.group(1)) == 2 and m.group(1).startswith("0"):
            return h, mi  # "09:00" is clearly 24-hour
        return _guess_hour(h, period_hint), mi
    m = re.search(r"\b(\d{1,2})\s*o'?clock\b", t)  # "5 o'clock"
    if m and int(m.group(1)) <= 23:
        return _guess_hour(int(m.group(1)), period_hint), 0
    m = re.search(r"(?<!\w)(?:at|@|by|around)\s+(\d{1,2})\b(?!\s*(?:st|nd|rd|th|of|" + _UNIT_RE + r"|"
                  + _MON_RE + r"))", t)
    if m:  # "at 5" with no am/pm: guess the sensible one
        h = int(m.group(1))
        if h > 23:
            return None
        return _guess_hour(h, period_hint), 0
    return None


def parse_when(text, now=None):
    """Turn 'tomorrow at 3pm', 'in 20 minutes', 'friday 5:30pm', '25 october at 9am' into a datetime.
    Returns None when no time can be understood."""
    now = (now or datetime.now()).replace(second=0, microsecond=0)
    t = (text or "").lower()
    t = re.sub(r"\b([ap])\.m\.?", r"\1m", t)
    t = re.sub(r"[,;]", " ", t)
    t = re.sub(r"\s+", " ", t).strip()
    if not t:
        return None
    t = _spelled_times(t)

    # relative: "in 20 minutes", "in an hour", "in half an hour", "in 2 days"
    if re.search(r"\bin half an? hour\b", t):
        return now + timedelta(minutes=30)
    m = re.search(r"\bin (\d+|a|an|one|two|three|four|five|six|seven|eight|nine|ten|fifteen|twenty|thirty|forty five)"
                  r" ?(minute|min|hour|hr|day|week)s?\b", t)
    if m:
        n = int(m.group(1)) if m.group(1).isdigit() else _NUMWORDS[m.group(1)]
        unit = m.group(2)
        delta = {"minute": timedelta(minutes=n), "min": timedelta(minutes=n), "hour": timedelta(hours=n),
                 "hr": timedelta(hours=n), "day": timedelta(days=n), "week": timedelta(weeks=n)}[unit]
        return now + delta

    # which day?
    date = None
    explicit_day = False
    if re.search(r"\bday after tomorrow\b", t):
        date, explicit_day = now.date() + timedelta(days=2), True
    elif re.search(r"\btomorrow\b", t):
        date, explicit_day = now.date() + timedelta(days=1), True
    elif re.search(r"\b(today|tonight|this (?:morning|afternoon|evening))\b", t):
        date, explicit_day = now.date(), True
    if date is None:
        m = (re.search(r"\b(\d{1,2})(?:st|nd|rd|th)?\s+(?:of\s+)?(" + _MON_RE + r")\b", t)
             and re.search(r"\b(?P<d>\d{1,2})(?:st|nd|rd|th)?\s+(?:of\s+)?(?P<m>" + _MON_RE + r")\b", t)) \
            or re.search(r"\b(?P<m>" + _MON_RE + r")\s+(?P<d>\d{1,2})(?:st|nd|rd|th)?\b", t)
        if m:
            day, month = int(m.group("d")), _MONTHS[m.group("m")]
            try:
                date = datetime(now.year, month, day).date()
                if date < now.date():
                    date = datetime(now.year + 1, month, day).date()
                explicit_day = True
            except ValueError:
                return None
    if date is None:
        m = None
        for cand in re.finditer(r"\b(?:(next|this|on|coming)\s+)?(" + _DAY_RE + r")\b", t):
            if cand.group(2) not in _AMBIGUOUS_DAYS or cand.group(1):
                m = cand
                break
        if m:
            wd = _WEEKDAYS[m.group(2)]
            ahead = (wd - now.weekday()) % 7
            if ahead == 0 and m.group(1) == "next":
                ahead = 7
            date, explicit_day = now.date() + timedelta(days=ahead), True

    period = None
    for word, key in (("morning", "morning"), ("afternoon", "afternoon"), ("evening", "evening"),
                      ("tonight", "night"), ("night", "night")):
        if re.search(r"\b" + word + r"\b", t):
            period = key
            break
    if re.search(r"\bpm\b", t):
        period = "pm"
    clock = _time_of_day(t, period)
    if clock is None and period:
        clock = {"morning": (9, 0), "afternoon": (15, 0), "evening": (18, 0), "night": (20, 0)}.get(period)
    if date is None and clock is None:
        return None
    if clock is None:
        clock = (9, 0)  # a day with no time: 9 in the morning
    if date is None:
        date = now.date()
    result = datetime.combine(date, datetime.min.time()).replace(hour=clock[0], minute=clock[1])
    if result <= now:
        if not explicit_day:
            result += timedelta(days=1)  # "at 5pm" said at 6pm means tomorrow
        elif date == now.date() and not re.search(r"\b(today|tonight|this)\b", t):
            result += timedelta(days=7)
        elif result <= now:
            return None  # asked for a time today that already passed
    return result


def fmt_when(dt, now=None):
    now = now or datetime.now()
    clock = dt.strftime("%I:%M %p").lstrip("0")
    days = (dt.date() - now.date()).days
    if days == 0:
        return f"today at {clock}"
    if days == 1:
        return f"tomorrow at {clock}"
    if 1 < days < 7:
        return f"{dt.strftime('%A')} at {clock}"
    return f"{dt.strftime('%A %d %B')} at {clock}"


# ---------- storage ----------
def _load():
    try:
        data = json.loads(FILE.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except (OSError, ValueError):
        return []


def _save(items):
    OUT.mkdir(parents=True, exist_ok=True)
    tmp = FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(items, indent=2), encoding="utf-8")
    os.replace(tmp, FILE)


def add(text, when_dt):
    items = _load()
    iso = when_dt.strftime("%Y-%m-%dT%H:%M")
    for it in items:  # same reminder twice is one reminder
        if not it.get("fired") and it.get("text", "").lower() == text.lower() and it.get("when") == iso:
            return it
    item = {"id": uuid.uuid4().hex[:8], "text": text, "when": iso, "fired": False,
            "created": datetime.now().strftime("%Y-%m-%dT%H:%M")}
    items.append(item)
    _save(items)
    return item


def upcoming(limit=6, now=None):
    now = now or datetime.now()
    out = []
    for it in _load():
        try:
            dt = datetime.strptime(it["when"], "%Y-%m-%dT%H:%M")
        except (KeyError, ValueError):
            continue
        if not it.get("fired") and dt >= now - timedelta(minutes=1):
            out.append((dt, it))
    out.sort(key=lambda p: p[0])
    return out[:limit]


def pop_due(now=None):
    """Return reminders whose time has come (marking them fired). Each is (item, minutes_late)."""
    now = now or datetime.now()
    items, due = _load(), []
    for it in items:
        if it.get("fired"):
            continue
        try:
            dt = datetime.strptime(it["when"], "%Y-%m-%dT%H:%M")
        except (KeyError, ValueError):
            continue
        if dt <= now:
            it["fired"] = True
            due.append((it, int((now - dt).total_seconds() // 60)))
    if due:
        _save(items)
    return due