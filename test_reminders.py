"""
Unit tests for the plain-English time parser (jarvis_reminders.parse_when).

Run:  python test_reminders.py
Prints a before/after-friendly table and exits with code 1 if anything fails.
"""

import sys
from datetime import datetime

import jarvis_reminders

NOW = datetime(2026, 10, 23, 9, 0)  # Friday 9:00 AM, same fixed "now" as eval_extraction.py


def dt(day, hour, minute=0):
    return datetime(2026, 10, day, hour, minute)


# (label, text, check(result) -> bool, expected description)
CASES = [
    # --- the bugs found by testing the parser directly (plus 'friday at 5' as a control that already worked) ---
    ("BUG 'friday at five'", "friday at five", lambda r: r == dt(23, 17), "Fri Oct 23, 5:00 PM"),
    ("control 'friday at 5'", "friday at 5", lambda r: r == dt(23, 17), "Fri Oct 23, 5:00 PM"),
    ("BUG 'around 5 minutes'", "around 5 minutes", lambda r: r is None, "None (not a clock time)"),
    ("BUG 'he sat down on friday'", "he sat down on friday", lambda r: r is not None and r.weekday() == 4,
     "a Friday (not Saturday)"),
    ("BUG 'five thirty pm'", "five thirty pm", lambda r: r == dt(23, 17, 30), "today, 5:30 PM"),

    # --- related cases fixed by the same change ---
    ("'at five thirty'", "remind me at five thirty", lambda r: r == dt(23, 17, 30), "today, 5:30 PM"),
    ("'at 5:30' no am/pm", "friday at 5:30", lambda r: r == dt(23, 17, 30), "Fri Oct 23, 5:30 PM"),
    ("'five o'clock'", "at five o'clock", lambda r: r == dt(23, 17), "today, 5:00 PM"),
    ("'on sat at 4pm'", "on sat at 4pm", lambda r: r == dt(24, 16), "Sat Oct 24, 4:00 PM"),

    # --- things that must NOT change (regression guards) ---
    ("'tomorrow at 3pm'", "tomorrow at 3pm", lambda r: r == dt(24, 15), "Sat Oct 24, 3:00 PM"),
    ("'in 20 minutes'", "in 20 minutes", lambda r: r == dt(23, 9, 20), "today, 9:20 AM"),
    ("'in five minutes'", "in five minutes", lambda r: r == dt(23, 9, 5), "today, 9:05 AM"),
    ("'17:30'", "tomorrow 17:30", lambda r: r == dt(24, 17, 30), "Sat Oct 24, 5:30 PM"),
    ("'09:00'", "tomorrow at 09:00", lambda r: r == dt(24, 9), "Sat Oct 24, 9:00 AM"),
    ("'friday 5:30pm'", "friday 5:30pm", lambda r: r == dt(23, 17, 30), "Fri Oct 23, 5:30 PM"),
    ("'25 october at 9am'", "25 october at 9am", lambda r: r == dt(25, 9), "Sun Oct 25, 9:00 AM"),
    ("'next monday'", "next monday", lambda r: r == dt(26, 9), "Mon Oct 26, 9:00 AM"),
    ("no time at all", "sometime soon", lambda r: r is None, "None"),
]


def main():
    failed = 0
    print(f"{'case':<30} {'input':<26} {'got':<18} {'expected':<26} result")
    print("-" * 110)
    for label, text, check, expected in CASES:
        got = jarvis_reminders.parse_when(text, now=NOW)
        ok = bool(check(got))
        failed += not ok
        shown = got.strftime("%a %b %d %I:%M %p") if got else "None"
        print(f"{label:<30} {text:<26} {shown:<18} {expected:<26} {'PASS' if ok else 'FAIL'}")
    print("-" * 110)
    print(f"{len(CASES) - failed}/{len(CASES)} passed")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
