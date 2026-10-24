"""
Jarvis eval: how well does a model turn a spoken to-do list into the right tasks?

Put this file and eval_set.json in the same folder as day1_voice_to_tasks.py.

Usage:
  python eval_extraction.py --models gemma3:4b gemma3:12b
  python eval_extraction.py --models gemma3:4b --code      # also test the code handler

Outputs: eval_results_<label>.json and .md (use --label so runs never overwrite each other) (paste the table into your post).
"""

import argparse
import json
import time
from collections import Counter
from datetime import datetime
from pathlib import Path

import jarvis_reminders
from day1_voice_to_tasks import extract_tasks

HERE = Path(__file__).parent
EVAL_FILE = HERE / "eval_set.json"
NOW = datetime(2026, 10, 23, 9, 0)  # fixed "now" so time parsing is repeatable

CODE_PROMPTS = [
    ("Calculator", "A calculator with add, subtract, multiply and divide functions."),
    ("Prime checker", "A function that checks whether a number is prime."),
    ("Palindrome check", "A function that checks if a string is a palindrome."),
    ("Fibonacci", "A function that returns the first n Fibonacci numbers."),
    ("FizzBuzz", "FizzBuzz for numbers 1 to 30."),
    ("Word counter", "A function that counts how often each word appears in a sentence."),
    ("Temperature converter", "Convert Celsius to Fahrenheit and back."),
    ("Bubble sort", "Implement bubble sort and test it on a few lists."),
]


def parsed(text):
    return jarvis_reminders.parse_when(text, now=NOW) if text else None


def score_case(pred, gold):
    """Compare predicted tasks (pydantic) with gold tasks (dicts)."""
    pred_types = Counter(t.type for t in pred)
    gold_types = Counter(g["type"] for g in gold)
    overlap = sum((pred_types & gold_types).values())

    # pair tasks of the same type in order of appearance
    by_type = {}
    for t in pred:
        by_type.setdefault(t.type, []).append(t)
    when_total = when_ok = clar_total = clar_ok = 0
    for g in gold:
        candidates = by_type.get(g["type"], [])
        if not candidates:
            continue
        p = candidates.pop(0)
        if g["type"] == "reminder":
            when_total += 1
            # same fallback the real pipeline uses (day3 handle_reminder)
            pw = parsed(p.when) or parsed(f"{p.details} {p.title}")
            gw = parsed(g.get("when", ""))
            when_ok += int(gw == pw)
        clar_total += 1
        clar_ok += int(p.needs_clarification == g.get("needs_clarification", False))

    return {
        "overlap": overlap, "pred_n": len(pred), "gold_n": len(gold),
        "exact_types": pred_types == gold_types,
        "when_total": when_total, "when_ok": when_ok,
        "clar_total": clar_total, "clar_ok": clar_ok,
    }


def is_real(case):
    """A case is a real voice note if it says so ("real": true/false) or carries the "source" audio
    filename that collect_transcripts.py writes. Everything else is a hand-written synthetic case."""
    return bool(case.get("real", "source" in case))


def frac(a, b):
    return f"{a}/{b} = {100 * a / b:.0f}%" if b else "n/a"


def pct(a, b):
    return f"{100 * a / b:.0f}%" if b else "n/a"


def eval_extraction(model, cases):
    totals = Counter()
    seconds, failures = [], []
    by_group = {"real": [0, 0], "synthetic": [0, 0]}  # [exact, total]
    for i, case in enumerate(cases, 1):
        group = "real" if is_real(case) else "synthetic"
        by_group[group][1] += 1
        start = time.perf_counter()
        try:
            for attempt in range(3):  # timeouts / dropped Ollama connections are retried, not scored as model errors
                try:
                    pred = extract_tasks(case["transcript"], model).tasks
                    break
                except RuntimeError:
                    raise  # real bad-JSON failure after the model's own retries
                except Exception as e:
                    if attempt == 2:
                        raise
                    print(f"  case {i}: infrastructure error ({type(e).__name__}), retrying in 10s...")
                    time.sleep(10)
        except Exception as e:
            totals["invalid_json"] += 1
            failures.append({"case": i, "problem": f"invalid output: {e}"})
            seconds.append(time.perf_counter() - start)
            continue
        seconds.append(time.perf_counter() - start)
        s = score_case(pred, case["gold"])
        by_group[group][0] += int(s["exact_types"])
        for k, v in s.items():
            totals[k] += int(v)
        if not s["exact_types"] or s["when_ok"] < s["when_total"]:
            failures.append({
                "case": i, "transcript": case["transcript"],
                "gold": [g["type"] for g in case["gold"]],
                "predicted": [(t.type, t.when) for t in pred],
            })
    n = len(cases)
    precision = totals["overlap"] / totals["pred_n"] if totals["pred_n"] else 0
    recall = totals["overlap"] / totals["gold_n"] if totals["gold_n"] else 0
    return {
        "model": model, "cases": n,
        "valid_json": pct(n - totals["invalid_json"], n),
        "exact_task_list": pct(totals["exact_types"], n),
        "exact_real": frac(*by_group["real"]),
        "exact_synthetic": frac(*by_group["synthetic"]),
        "type_precision": f"{100 * precision:.0f}%",
        "type_recall": f"{100 * recall:.0f}%",
        "reminder_time_correct": pct(totals["when_ok"], totals["when_total"]),
        "clarification_flag_correct": pct(totals["clar_ok"], totals["clar_total"]),
        "avg_seconds_per_case": round(sum(seconds) / max(len(seconds), 1), 1),
        "failures": failures,
    }


def eval_code(model):
    from day3_run_tasks import handle_code  # imported here so extraction eval works without it

    states = Counter()
    seconds = []
    for title, details in CODE_PROMPTS:
        start = time.perf_counter()
        try:
            out = handle_code({"type": "code", "title": title, "details": details}, model)
            note = out["note"]
            if note.startswith("Ran and passed"):
                state = "tested"
            elif note.startswith("Ran without"):
                state = "ran_unverified"
            elif note.startswith("Not run"):
                state = "blocked"
            else:
                state = "failed"
        except Exception:
            state = "error"
        seconds.append(time.perf_counter() - start)
        states[state] += 1
        print(f"  code: {title}: {state}")
    return {"model": model, "code_tasks": len(CODE_PROMPTS), **states,
            "avg_seconds_per_task": round(sum(seconds) / len(seconds), 1)}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--models", nargs="+", default=["gemma3:4b"])
    parser.add_argument("--code", action="store_true", help="also run the code-handler test")
    parser.add_argument("--label", default="run", help="name for this run; saves eval_results_<label>.md/.json")
    args = parser.parse_args()

    cases = json.loads(EVAL_FILE.read_text(encoding="utf-8"))
    n_real = sum(is_real(c) for c in cases)
    print(f"Loaded {len(cases)} labeled transcripts ({n_real} real voice notes, {len(cases) - n_real} synthetic).")

    results, code_results = [], []
    for model in args.models:
        print(f"\n=== {model} ===")
        results.append(eval_extraction(model, cases))
        if args.code:
            code_results.append(eval_code(model))

    (HERE / f"eval_results_{args.label}.json").write_text(
        json.dumps({"extraction": results, "code": code_results}, indent=2), encoding="utf-8")

    cols = ["valid_json", "exact_task_list", "type_precision", "type_recall",
            "reminder_time_correct", "clarification_flag_correct", "avg_seconds_per_case"]
    lines = ["| model | " + " | ".join(cols) + " |", "|" + "---|" * (len(cols) + 1)]
    for r in results:
        lines.append(f"| {r['model']} | " + " | ".join(str(r[c]) for c in cols) + " |")
    lines += ["", "Exact task list, split by where the transcript came from (headline = real voice notes):", "",
              "| model | real voice notes | synthetic |", "|---|---|---|"]
    for r in results:
        lines.append(f"| {r['model']} | {r['exact_real']} | {r['exact_synthetic']} |")
    if code_results:
        lines += ["", "Code handler:", "", "```", json.dumps(code_results, indent=2), "```"]
    lines += ["", "## Misses (read these, they are your best material)"]
    for r in results:
        for f in r["failures"]:
            lines.append(f"- {r['model']} case {f['case']}: {json.dumps(f, ensure_ascii=False)}")
    table = "\n".join(lines)
    (HERE / f"eval_results_{args.label}.md").write_text(table, encoding="utf-8")
    print("\n" + table)
    print(f"\nSaved eval_results_{args.label}.md and .json")


if __name__ == "__main__":
    main()