"""
Jarvis - run the whole night in one command: voice note -> tasks -> work -> morning report.

Usage:
  python run_overnight.py my_voice.ogg
  python run_overnight.py my_voice.ogg --model gemma3:12b --whisper medium.en

Put your exported chat in ~/jarvis_output/inputs/ first if you have a chat-summary task.
"""

import argparse
import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).parent
LOG_FILE = Path.home() / "jarvis_output" / "jarvis.log"


def fmt(seconds: float) -> str:
    minutes, secs = divmod(int(round(seconds)), 60)
    return f"{minutes}m {secs:02d}s" if minutes else f"{secs}s"


def log(msg: str):
    line = f"[{datetime.now().strftime('%H:%M:%S')}] {msg}"
    print(line)
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    with LOG_FILE.open("a", encoding="utf-8") as f:
        f.write(line + "\n")


def main():
    parser = argparse.ArgumentParser(description="Jarvis overnight run")
    parser.add_argument("audio", help="Your voice note (ogg, wav, mp3, m4a)")
    parser.add_argument("--model", default="gemma3:4b", help="Ollama model name")
    parser.add_argument("--whisper", default=os.environ.get("JARVIS_WHISPER", "small.en"),
                        help="Whisper size: tiny.en/base.en/small.en/medium.en")
    args = parser.parse_args()

    start = time.perf_counter()
    log(f"=== Overnight run started (model {args.model}, whisper {args.whisper}) ===")

    tasks_file = Path.home() / "jarvis_output" / "tasks.json"
    wall_start = time.time()
    step1 = subprocess.run([
        sys.executable, str(HERE / "day1_voice_to_tasks.py"), args.audio,
        "--model", args.model, "--whisper", args.whisper,
    ])
    if step1.returncode != 0:
        saved_fresh = tasks_file.exists() and tasks_file.stat().st_mtime >= wall_start
        if not saved_fresh:
            sys.exit(f"Step 1 (voice to tasks) failed (exit code {step1.returncode}). Nothing was done.")
        # The tasks were written, so the work succeeded; the crash happened afterwards
        # (some native audio libraries crash while the program is shutting down).
        log(f"WARNING: step 1 exited with code {step1.returncode} AFTER saving tasks.json. "
            "Continuing because the tasks were written.")
    after_step1 = time.perf_counter()
    log(f"Step 1 (voice to tasks) took {fmt(after_step1 - start)}")

    step2 = subprocess.run([
        sys.executable, str(HERE / "day3_run_tasks.py"), "--model", args.model,
    ])
    total = time.perf_counter() - start
    if step2.returncode != 0:
        sys.exit("Step 2 (doing the tasks) failed. Check jarvis.log.")

    log(f"Step 2 (tasks and report) took {fmt(total - (after_step1 - start))}")
    log(f"=== Whole night, voice note to morning report: {fmt(total)} ===")
    print(f"\nOpen {Path.home() / 'jarvis_output' / 'morning_report.md'}")


if __name__ == "__main__":
    main()