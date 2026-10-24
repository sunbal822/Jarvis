"""
Jarvis - Day 3: do the tasks, with a smarter morning report

v6: new handlers for message, reminder and other; code is tested (up to 3 tries) before it is called done.

Replaces day2_run_tasks.py. Same handlers, plus:
  - timing for every task and for the whole run
  - the email handler counts placeholders you still need to fill in
  - the chat summary's "STILL OPEN" items are pulled into the report
  - summaries are flagged for approval (small models can misread chats)
  - "file" tasks create a starter template (e.g. homework_1.md) in ~/jarvis_output/files/

Usage:
  python day3_run_tasks.py
  python day3_run_tasks.py --model gemma3:12b
  python day3_run_tasks.py --only chat_summary
"""

import argparse
import json
import re
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

import ollama

import jarvis_reminders

OUTPUT_DIR = Path.home() / "jarvis_output"
CODE_DIR = OUTPUT_DIR / "code"
DRAFTS_DIR = OUTPUT_DIR / "drafts"
SUMMARIES_DIR = OUTPUT_DIR / "summaries"
INPUTS_DIR = OUTPUT_DIR / "inputs"
FILES_DIR = OUTPUT_DIR / "files"
MESSAGES_DIR = OUTPUT_DIR / "messages"
NOTES_DIR = OUTPUT_DIR / "notes"
LOG_FILE = OUTPUT_DIR / "jarvis.log"

OLLAMA_TIMEOUT = 900  # seconds per model call; a stuck call fails that task, not the night
MAX_CHAT_CHARS = 24000  # keep the prompt inside the model's context window


# ---------- helpers ----------

def log(msg: str):
    line = f"[{datetime.now().strftime('%H:%M:%S')}] {msg}"
    print(line)
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    with LOG_FILE.open("a", encoding="utf-8") as f:
        f.write(line + "\n")


def fmt_duration(seconds: float) -> str:
    seconds = int(round(seconds))
    minutes, secs = divmod(seconds, 60)
    return f"{minutes}m {secs:02d}s" if minutes else f"{secs}s"


def safe_write(path: Path, text: str) -> Path:
    """The ONLY way this program writes files. Refuses anything outside OUTPUT_DIR."""
    resolved = path.resolve()
    if OUTPUT_DIR.resolve() not in resolved.parents:
        raise PermissionError(f"Refusing to write outside {OUTPUT_DIR}: {resolved}")
    resolved.parent.mkdir(parents=True, exist_ok=True)
    resolved.write_text(text, encoding="utf-8")
    return resolved


def slugify(text: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")
    return slug[:40] or "task"


def find_placeholders(text: str) -> list:
    """Find things like [YOUR NAME] that the human still has to fill in."""
    return sorted(set(re.findall(r"\[[^\]\n]{2,60}\]", text)))


def ask(model: str, system: str, user: str, temperature: float = 0.2) -> str:
    response = ollama.Client(timeout=OLLAMA_TIMEOUT).chat(
        model=model,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        options={"temperature": temperature, "num_ctx": 8192},
    )
    return response.message.content.strip()


def extract_code(text: str) -> str:
    match = re.search(r"```(?:python)?\s*\n(.*?)```", text, re.DOTALL)
    return (match.group(1) if match else text).strip() + "\n"


def run_python(path: Path):
    """Run a script with no input and a time limit. Returns (ok, output)."""
    try:
        result = subprocess.run(
            [sys.executable, str(path)],
            capture_output=True,
            text=True,
            timeout=20,
            stdin=subprocess.DEVNULL,
            cwd=path.parent,
        )
        output = (result.stdout + result.stderr).strip()
        return result.returncode == 0, output
    except subprocess.TimeoutExpired:
        return False, "Timed out after 20 seconds."


# ---------- handlers ----------

CODE_SYSTEM = """You are a careful Python programmer.
Write a single, complete Python 3 script for the task.
Rules:
- The script must run on its own WITHOUT any user input. Never call input().
- If the program is interactive by nature (like a calculator), put the logic in functions
  and add a demo at the bottom that runs several example cases and prints the results.
- Use only the standard library.
- Never start a web server, a GUI window, a browser, or another program. If the task is an app (a login page, a
  to-do app, a game), build it as plain functions with in-memory data (for example a dictionary of users), and
  demo it with example calls.
- Add at least 3 `assert` statements that check results you are CERTAIN are correct,
  then print "ALL TESTS PASSED" as the very last line.
- Never delete or move files, use the network, or start other programs.
- Reply with ONLY one ```python code block. No explanation."""

MAX_CODE_ATTEMPTS = 3
RISKY_CODE = re.compile(
    r"(os\.(?:remove|unlink|rmdir|removedirs|system|popen|kill|startfile|rename|replace|walk)|shutil\.|subprocess|"
    r"\bsocket\b|\brequests\b|urllib|http\.client|ftplib|smtplib|ctypes|winreg|\beval\(|\bexec\(|__import__|"
    r"\bpip\b|pathlib\.Path\.home|expanduser)")


def check_code(path: Path, code: str):
    """Returns (state, output). state: tested | ran | failed | blocked."""
    try:
        compile(code, str(path), "exec")
    except SyntaxError as e:
        return "failed", f"SyntaxError: {e.msg} on line {e.lineno}"
    risky = RISKY_CODE.search(code)
    if risky:
        return "blocked", f"The script uses '{risky.group(0)}', which Jarvis will not run by itself."
    ok, output = run_python(path)
    if not ok:
        return "failed", output
    if "ALL TESTS PASSED" in output:
        return "tested", output
    return "ran", output


def handle_code(task: dict, model: str) -> dict:
    slug = slugify(task["title"])
    code_path = CODE_DIR / f"{slug}.py"
    prompt = f"Task: {task['title']}\nDetails: {task['details']}"
    rank = {"tested": 3, "ran": 2, "failed": 1, "blocked": 0}
    best = None  # (state, code, output)
    code, state, output = "", "", ""

    for attempt in range(1, MAX_CODE_ATTEMPTS + 1):
        if attempt == 1:
            user = prompt
        elif state == "blocked":
            user = (f"{prompt}\n\nYour previous script:\n```python\n{code}```\n{output}\n"
                    "Rewrite it without that. Return the full script.")
        elif state == "ran":
            user = (f"{prompt}\n\nYour previous script ran but has no self-checks:\n```python\n{code}```\n"
                    "Add at least 3 assert statements with answers you are certain of, then print "
                    "ALL TESTS PASSED as the last line. Return the full script.")
        else:
            user = (f"{prompt}\n\nYour previous script:\n```python\n{code}```\n"
                    f"It failed with:\n{output[-1500:]}\n\nFix it and return the full corrected script.")
        code = extract_code(ask(model, CODE_SYSTEM, user))
        safe_write(code_path, code)
        state, output = check_code(code_path, code)
        log(f"  code attempt {attempt}: {state.upper()}")
        if best is None or rank[state] > rank[best[0]]:
            best = (state, code, output)
        if state == "tested":
            break

    state, code, output = best
    safe_write(code_path, code)  # keep the best version, not just the last
    safe_write(CODE_DIR / f"{slug}.run_output.txt", output or "(no output)")
    if state == "tested":
        status, note = "done", f"Ran and passed its own tests (attempt {attempt})."
    elif state == "ran":
        status, note = "done", "Ran without errors, but it has no working self-tests, so the results are NOT verified."
    elif state == "blocked":
        status, note = "failed", f"Not run: {output}"
    else:
        status, note = "failed", f"Still failing after {MAX_CODE_ATTEMPTS} tries: {output[-200:]}"
    return {
        "status": status,
        "file": str(code_path),
        "note": note,
        "needs_approval": True,
        "approval_reason": "AI-written code. Read it before you use it." if state != "blocked"
                           else "Jarvis did NOT run this because it uses risky calls. Read it carefully first.",
        "help": [],
    }


EMAIL_SYSTEM = """You draft short, polite, professional emails for a university student.
Rules:
- Use only the facts you are given. Do not invent reasons, dates, names, or promises.
- Where information is missing, put a placeholder in square brackets, like [REASON] or [YOUR NAME].
- Write a specific subject line that says what the email is about, not just "Email to ...".
- Format: first line "Subject: ...", then a blank line, then the email body."""


def handle_email(task: dict, model: str) -> dict:
    prompt = f"Draft this email.\nTitle: {task['title']}\nDetails: {task['details']}"
    draft = ask(model, EMAIL_SYSTEM, prompt)
    header = "*** DRAFT - NOT SENT. Review and edit before sending it yourself. ***\n\n"
    path = safe_write(DRAFTS_DIR / f"{slugify(task['title'])}.txt", header + draft)

    placeholders = find_placeholders(draft)
    note = "Draft saved. Nothing was sent."
    if placeholders:
        note += f" {len(placeholders)} placeholder(s) to fill in: {', '.join(placeholders)}"
    return {
        "status": "done",
        "file": str(path),
        "note": note,
        "needs_approval": True,
        "approval_reason": "Draft only. Nothing was sent. You send it yourself.",
        "help": [f"Fill in {p} in the email draft" for p in placeholders],
    }


SUMMARY_SYSTEM = """You summarize group chats for someone who was asleep and is a member of the chat.
Start with one or two sentences: "While you were asleep, ..."
Then use these sections:
- IMPORTANT: announcements and facts they must know
- DEADLINES AND DATES
- SHARED RESOURCES: files or links that were shared (skip this section if none)
- STILL OPEN: questions or requests that are still unanswered at the END of the chat.
  A question that someone answered later in the chat is NOT open. Do not list it.
Rules: be short, name who said what, use only what is in the chat, and do not guess.
Do not wrap your answer in a code block."""


def extract_open_items(summary: str) -> list:
    """Pull the STILL OPEN section out of the summary as a list of short lines."""
    match = re.search(
        r"STILL OPEN\W*(.*?)(?=\n\s*\W*[A-Z][A-Z &/]{3,}\W*:|\Z)", summary, re.DOTALL
    )
    if not match:
        return []
    items = []
    for line in match.group(1).splitlines():
        line = line.strip(" -*\u2022\t")
        if line and line.lower().rstrip(".") not in ("none", "nothing", "n/a"):
            items.append(line)
    return items


def handle_chat_summary(task: dict, model: str) -> dict:
    INPUTS_DIR.mkdir(parents=True, exist_ok=True)
    chats = sorted(INPUTS_DIR.glob("*.txt"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not chats:
        return {
            "status": "needs_input",
            "file": None,
            "note": f"No chat export found. Put an exported chat .txt file in {INPUTS_DIR}",
            "needs_approval": False,
            "approval_reason": "",
            "help": [f"Add an exported chat (.txt) to {INPUTS_DIR}, then rerun with --only chat_summary"],
        }

    chat_file = chats[0]
    text = chat_file.read_text(encoding="utf-8", errors="ignore")
    truncated = len(text) > MAX_CHAT_CHARS
    if truncated:
        text = text[-MAX_CHAT_CHARS:]  # keep the most recent messages

    summary = ask(model, SUMMARY_SYSTEM, f"Chat export:\n\n{text}")
    summary = re.sub(r"^```(?:markdown)?\s*|\s*```$", "", summary).strip()

    note = f"Summarized {chat_file.name}."
    if truncated:
        note += " (Chat was long, so only the most recent part was used.)"
    path = safe_write(SUMMARIES_DIR / f"{slugify(task['title'])}.md", summary + "\n")

    open_items = extract_open_items(summary)
    return {
        "status": "done",
        "file": str(path),
        "note": note,
        "needs_approval": True,
        "approval_reason": "Small models can misread chats. Skim the original for anything critical.",
        "help": [f"Still open in the chat: {item}" for item in open_items],
    }


FILE_SYSTEM = """You create a starter template for a university assignment document.
Rules:
- Use only the facts you are given. Do NOT write the assignment's answers or content.
- Use placeholders in square brackets for anything unknown, like [COURSE NAME], [YOUR NAME], [DUE DATE].
- Include: a title block, a short Objective section, an empty Work section, and a submission checklist.
- Plain Markdown only. Keep it under 30 lines. Do not wrap it in a code block."""


def pick_filename(task: dict) -> str:
    """Use the name the speaker gave ("called homework 1"), else the task title."""
    match = re.search(r"called\s+['\"]?([A-Za-z0-9 _.-]{1,40})", task["details"], re.IGNORECASE)
    if match:
        raw = re.split(r"\s+(?:for|and|please|with|to|on)\b", match.group(1), flags=re.IGNORECASE)[0]
    else:
        raw = re.sub(r"\s+file$", "", task["title"], flags=re.IGNORECASE)
    return slugify(raw)


FILE_GENERAL_SYSTEM = """You create the contents of a file the user asked for.
Rules:
- Use only the facts you are given. Put anything unknown in square brackets, like [NAME].
- Output ONLY the file contents, in the format of the requested file type. No explanation, no code block."""

ASSIGNMENT_WORDS = re.compile(r"homework|assignment|lab report|quiz|exam|coursework|professor|due date|course", re.I)


def pick_extension(task: dict) -> str:
    text = f"{task['title']} {task['details']}".lower()
    m = re.search(r"\.(md|txt|csv|json)\b", text)
    if m:
        return m.group(1)
    for word, ext in (("csv", "csv"), ("json", "json"), ("text file", "txt"), ("txt", "txt")):
        if word in text:
            return ext
    return "md"


def handle_file(task: dict, model: str) -> dict:
    name = pick_filename(task)
    ext = pick_extension(task)
    assignment = bool(ASSIGNMENT_WORDS.search(f"{task['title']} {task['details']}"))
    prompt = f"Create the {'template' if assignment else 'file'}.\nTitle: {task['title']}\nDetails: {task['details']}"
    content = ask(model, FILE_SYSTEM if assignment else FILE_GENERAL_SYSTEM, prompt)
    content = re.sub(r"^```[a-z]*\s*|\s*```$", "", content).strip()
    path = safe_write(FILES_DIR / f"{name}.{ext}", content + "\n")

    placeholders = find_placeholders(content)
    note = f"Created {name}.{ext}." + (" (Starter template only.)" if assignment else "")
    help_items = []
    if placeholders:
        help_items.append(f"Fill in {len(placeholders)} placeholder(s) in {name}.{ext}: {', '.join(placeholders)}")
    return {
        "status": "done",
        "file": str(path),
        "note": note,
        "needs_approval": True,
        "approval_reason": ("Starter template only. Jarvis did not do the assignment itself." if assignment
                            else "AI-written file. Check it before you use it."),
        "help": help_items,
    }


MESSAGE_SYSTEM = """You write short text / chat messages (WhatsApp, SMS, DM) in the first person, as the user.
Rules:
- Sound like a normal person texting: friendly and brief (1 to 4 sentences). More formal only if the details ask.
- NO subject line, NO "Dear", NO email sign-off. No emojis unless asked.
- Use only the facts you are given. Do not invent reasons, times, or promises.
- If something needed is missing, put a placeholder in square brackets, like [TIME].
- Output ONLY the message text."""


def handle_message(task: dict, model: str) -> dict:
    prompt = f"Write this message.\nTitle: {task['title']}\nDetails: {task['details']}"
    text = ask(model, MESSAGE_SYSTEM, prompt, temperature=0.4)
    text = re.sub(r"^```[a-z]*\s*|\s*```$", "", text).strip().strip('"')
    path = safe_write(MESSAGES_DIR / f"{slugify(task['title'])}.txt", text + "\n")
    placeholders = find_placeholders(text)
    note = "Message written. Nothing was sent."
    if placeholders:
        note += f" {len(placeholders)} placeholder(s) to fill in: {', '.join(placeholders)}"
    return {
        "status": "done", "file": str(path), "note": note,
        "needs_approval": True,
        "approval_reason": "Message only. Nothing was sent. You send it yourself.",
        "help": [f"Fill in {p} in the message" for p in placeholders],
    }


def handle_reminder(task: dict, model: str) -> dict:
    what = (task.get("details") or task["title"]).strip()
    when_text = (task.get("when") or "").strip()
    when = jarvis_reminders.parse_when(when_text) if when_text else None
    if when is None:  # the model may have left the time in the details or title instead
        when = jarvis_reminders.parse_when(f"{task.get('details', '')} {task['title']}")
    if when is None:
        return {
            "status": "needs_input", "file": None,
            "note": "I couldn't work out WHEN. Say it like 'tomorrow at 3pm' or 'in 20 minutes'.",
            "needs_approval": False, "approval_reason": "",
            "help": [f"Tell me the time for: {what}"],
        }
    item = jarvis_reminders.add(what, when)
    nice = jarvis_reminders.fmt_when(when)
    log(f"  reminder saved: {what} ({item['when']})")
    return {
        "status": "done", "file": None,
        "note": f"Reminder set for {nice}: {what}",
        "needs_approval": False, "approval_reason": "", "help": [],
    }


OTHER_SYSTEM = """You are a helpful assistant. Do the task as plain text only: answer the question, explain,
plan, or brainstorm. Be concise and practical. Use only facts you are given; say so when you are unsure.
You cannot browse the internet, send anything, or run anything. Do not claim to have done actions.
Do not use a code block around your whole answer."""


def handle_other(task: dict, model: str) -> dict:
    prompt = f"Task: {task['title']}\nDetails: {task['details']}"
    text = ask(model, OTHER_SYSTEM, prompt, temperature=0.4)
    text = re.sub(r"^```(?:markdown)?\s*|\s*```$", "", text).strip()
    path = safe_write(NOTES_DIR / f"{slugify(task['title'])}.md", text + "\n")
    return {
        "status": "done", "file": str(path), "note": "Written up as a note.",
        "needs_approval": True,
        "approval_reason": "A small local model wrote this from memory. Double-check any facts.",
        "help": [],
    }


def handle_unsupported(task: dict, model: str) -> dict:
    return {
        "status": "skipped",
        "file": None,
        "note": f"Task type '{task['type']}' is not supported yet.",
        "needs_approval": False,
        "approval_reason": "",
        "help": [],
    }


HANDLERS = {
    "code": handle_code,
    "email": handle_email,
    "message": handle_message,
    "reminder": handle_reminder,
    "other": handle_other,
    "chat_summary": handle_chat_summary,
    "file": handle_file,
}


# ---------- morning report ----------

def write_report(results: list, total_seconds: float, model: str):
    labels = {"done": "DONE", "failed": "FAILED", "needs_input": "NEEDS INPUT", "skipped": "SKIPPED"}
    lines = [
        f"# Morning Report - {datetime.now().strftime('%A, %d %B %Y')}",
        "",
        f"Ran {len(results)} task(s) in {fmt_duration(total_seconds)} on `{model}`, "
        "entirely on this laptop.",
        "",
        "## What I did",
    ]
    for r in results:
        took = f" - {fmt_duration(r['seconds'])}" if r.get("seconds") is not None else ""
        lines.append(f"- **[{labels.get(r['status'], r['status'])}]** {r['title']} ({r['type']}){took}")
        lines.append(f"  - {r['note']}")
        if r.get("file"):
            lines.append(f"  - File: `{r['file']}`")

    lines += ["", "## Needs your approval"]
    approvals = [r for r in results if r.get("needs_approval")]
    if approvals:
        for r in approvals:
            lines.append(f"- [ ] {r['title']}: {r.get('approval_reason', '')}")
    else:
        lines.append("- Nothing.")

    lines += ["", "## Needs your help"]
    help_items = []
    for r in results:
        if r["status"] in ("failed", "needs_input"):
            help_items.append(f"{r['title']}: {r['note']}")
        help_items.extend(r.get("help", []))
    if help_items:
        for item in help_items:
            lines.append(f"- {item}")
    else:
        lines.append("- Nothing.")

    report_path = safe_write(OUTPUT_DIR / "morning_report.md", "\n".join(lines) + "\n")
    log(f"Morning report written to {report_path}")


# ---------- main ----------

def main():
    parser = argparse.ArgumentParser(description="Jarvis Day 3: do the tasks")
    parser.add_argument("--model", default="gemma3:4b", help="Ollama model name")
    parser.add_argument("--only", help="Only run tasks of this type, e.g. chat_summary")
    args = parser.parse_args()

    tasks_file = OUTPUT_DIR / "tasks.json"
    if not tasks_file.exists():
        sys.exit(f"No tasks.json found at {tasks_file}. Run day1_voice_to_tasks.py first.")

    tasks = json.loads(tasks_file.read_text(encoding="utf-8"))["tasks"]
    if args.only:
        tasks = [t for t in tasks if t["type"] == args.only]
    log(f"Starting {len(tasks)} task(s) with {args.model}")

    run_start = time.perf_counter()
    results = []
    for i, task in enumerate(tasks, 1):
        log(f"Task {i}/{len(tasks)}: ({task['type']}) {task['title']}")
        handler = HANDLERS.get(task["type"], handle_unsupported)
        task_start = time.perf_counter()
        try:
            outcome = handler(task, args.model)
        except Exception as e:  # one bad task must never stop the rest
            log(f"  ERROR: {e}")
            outcome = {
                "status": "failed", "file": None, "note": f"Error: {e}",
                "needs_approval": False, "approval_reason": "", "help": [],
            }
        seconds = time.perf_counter() - task_start
        log(f"  finished in {fmt_duration(seconds)}")
        results.append({"title": task["title"], "type": task["type"], "seconds": seconds, **outcome})
    total_seconds = time.perf_counter() - run_start

    results_file = OUTPUT_DIR / "results.json"
    if args.only and results_file.exists():
        previous = json.loads(results_file.read_text(encoding="utf-8"))
        results = [r for r in previous if r["type"] != args.only] + results
        total_seconds += sum(r.get("seconds") or 0 for r in previous if r["type"] != args.only)

    safe_write(results_file, json.dumps(results, indent=2))
    write_report(results, total_seconds, args.model)
    log(f"All done in {fmt_duration(total_seconds)}.")


if __name__ == "__main__":
    main()