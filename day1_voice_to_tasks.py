"""
Jarvis - Day 1: voice note -> task list (JSON)

Everything runs locally:
  - faster-whisper transcribes your audio
  - an open-weight model (Gemma by default) via Ollama splits it into tasks

Usage:
  python day1_voice_to_tasks.py my_note.m4a
  python day1_voice_to_tasks.py --text "I need a calculator in python and an email to my professor"
"""

import argparse
import json
import os
import re
import sys
import time
from pathlib import Path
from typing import List, Literal

import ollama
from pydantic import BaseModel, ValidationError

OUTPUT_DIR = Path.home() / "jarvis_output"
OLLAMA_TIMEOUT = 150  # seconds per attempt; a runaway generation is cut off and retried instead of hanging
DEFAULT_WHISPER = os.environ.get("JARVIS_WHISPER", "small.en")  # same default as the Jarvis window

SYSTEM_PROMPT = """You are Jarvis, a careful assistant that turns a spoken to-do list into separate tasks.

Task types (use exactly one per task):
- "code": write a program or script (e.g. a calculator, a linked list, an assignment that is a program)
- "email": draft an email (NEVER send, drafts only). Use only when the speaker says email.
- "message": write a short text / chat message (WhatsApp, SMS, DM) to send to someone. Not an email.
- "reminder": remind me of something, set an alarm, or schedule a call / meeting / appointment at a time
- "chat_summary": summarize an actual group chat or conversation the speaker was part of (e.g. "summarize my group chat"). A summary of a topic, a subject, a lecture, or emails is NOT chat_summary, use "other"
- "file": create or organize a file or document, including compiling a written assignment, report or notes
- "other": anything else (questions, explanations, plans, research notes)

For each task return:
- type: one of the types above
- title: a short title (max 8 words)
- details: everything needed to do the task, using only what the speaker said
- when: ONLY for "reminder": the time words exactly as spoken (e.g. "tomorrow at 3pm", "in 20 minutes",
  "friday at 5"). Empty for every other type.
- needs_clarification: true if important info is missing (e.g. no recipient for an email, or no time for a reminder, or a day with no clock time)

For a "reminder", details = WHAT to be reminded of, without the time (e.g. "call Professor Khan", "go to work").

Rules:
- Do not invent details the speaker did not say.
- NEVER invent a reminder time. If the speaker gave no time, "when" is empty and needs_clarification is true. If they gave only a day and no clock time, "when" is just that day word, and needs_clarification is true. Only use time words the speaker actually said.
- Jarvis cannot read emails or the internet. A request to summarize emails, or to explain or summarize a topic, is "other", never "chat_summary".
- Pick the type from what the speaker wants done, not from a single word. The word "summary" alone does not mean chat_summary, and the word "assignment" alone does not mean code.
- "Attend a meeting at 5" or "dinner at 8" with a reminder wanted is a "reminder" with that time.
- One task per distinct request. Split combined requests.
- Return only JSON that matches the schema."""


class Task(BaseModel):
    type: Literal["code", "email", "message", "reminder", "chat_summary", "file", "other"]
    title: str
    details: str
    needs_clarification: bool = False
    when: str = ""


class TaskList(BaseModel):
    tasks: List[Task]


_CHAT_WORDS = re.compile(r"\b(chats?|group|conversations?|messages?|thread|whatsapp|discord|telegram|slack)\b", re.I)
_DAY_WORDS = ("monday tuesday wednesday thursday friday saturday sunday tomorrow today tonight "
              "january february march april may june july august september october november december").split()
_NUM_WORDS = {1: "one", 2: "two", 3: "three", 4: "four", 5: "five", 6: "six", 7: "seven", 8: "eight", 9: "nine",
              10: "ten", 11: "eleven", 12: "twelve", 15: "fifteen", 20: "twenty", 30: "thirty", 45: "forty"}


def _said(word_or_num, transcript):
    t = transcript.lower()
    if isinstance(word_or_num, int):
        return bool(re.search(rf"(?<!\d){word_or_num}(?!\d)", t)) or _NUM_WORDS.get(word_or_num, "@@") in t
    return bool(re.search(rf"\b{word_or_num}", t))


_CODE_WORDS = re.compile(r"\b(code|coding|program|programs|script|python|java|javascript|c\+\+|function|algorithm|app|"
                         r"calculator|linked list|sort|loop|class|bug|compile\s+(?:the\s+)?code)\b", re.I)


def validate_tasks(tasks: "TaskList", transcript: str) -> "TaskList":
    """Plain-code safety net for mistakes a small model keeps making despite the prompt."""
    for t in tasks.tasks:
        # 'summary' of a topic or of emails is not a chat summary; Jarvis can only summarize a real chat
        if t.type == "chat_summary" and not _CHAT_WORDS.search(transcript):
            t.type = "other"
        # 'assignment' alone does not mean code: with no programming words anywhere, it is a written document
        if t.type == "code" and not _CODE_WORDS.search(transcript):
            t.type = "file"
        # a written "assignment" that is explicitly a program (Python calculator, etc.) is code, not a document
        if t.type in ("file", "other") and re.search(r"\bassignment\b", f"{t.title} {t.details}", re.I) \
                and re.search(r"\b(python|java|javascript|c\+\+|calculator|program|script|code)\b",
                              f"{t.title} {t.details}", re.I):
            t.type = "code"
        # a reminder time may only use day/month words and numbers the speaker really said
        if t.type == "reminder" and t.when:
            w = t.when.lower()
            w = re.sub(r"(?<=\d):00(?::00)?\b", "", w)  # "7:00 PM" is just "7 PM"; ":00" minutes are not an invented number
            invented = any(d in w and not _said(d, transcript) for d in _DAY_WORDS) or \
                any(not _said(int(n), transcript) for n in re.findall(r"\d+", w))
            if invented:
                t.when = ""
                t.needs_clarification = True
    return tasks


def transcribe(audio_path: str, model_size: str, hint: str = "") -> str:
    from faster_whisper import WhisperModel

    print(f"[1/2] Transcribing with Whisper ({model_size})...")
    # int8 on CPU works everywhere. Change to device="cuda" if you have an NVIDIA GPU.
    model = WhisperModel(model_size, device="auto", compute_type="int8")
    prompt = f"Vocabulary: {hint}." if hint else "A spoken to-do list for a personal assistant."
    lang = None if model_size.endswith(".en") else "en"  # *.en models are English-only already
    segments, info = model.transcribe(audio_path, vad_filter=True, initial_prompt=prompt, language=lang,
                                      beam_size=5, condition_on_previous_text=False)
    text = " ".join(seg.text.strip() for seg in segments)
    print(f"      Detected language: {info.language}")
    return text.strip()


# Each attempt uses slightly different settings. Temperature 0 is deterministic, so if the model falls into an
# endless-repeat loop on one transcript it would loop the same way every time; later attempts break the loop.
# num_predict caps the answer length: a task list is short, so a runaway answer is cut off quickly.
_ATTEMPTS = [
    ({"temperature": 0, "num_ctx": 4096, "num_predict": 700}, ""),
    ({"temperature": 0.3, "num_ctx": 4096, "num_predict": 700, "repeat_penalty": 1.15, "seed": 7}, ""),
    ({"temperature": 0.6, "num_ctx": 4096, "num_predict": 700, "repeat_penalty": 1.2, "seed": 13}, ""),
    # last resort: shorter cap, strong repeat penalty, and an explicit reminder to keep every field short
    ({"temperature": 0.8, "num_ctx": 4096, "num_predict": 450, "repeat_penalty": 1.35, "seed": 21},
     "\n\n(Keep every field short, one sentence at most. Never count or repeat numbers. "
     "If a word looks misspelled, read it as the most likely normal word.)"),
]


def extract_tasks(transcript: str, model: str, retries: int = 3) -> TaskList:
    """Ask the model for tasks. Bad JSON, a timeout, or a dropped Ollama connection all trigger a retry
    with different sampling settings. If every attempt fails, the last real error is raised."""
    print(f"[2/2] Extracting tasks with {model}...")
    last_error = None
    for attempt in range(retries + 1):
        options, suffix = _ATTEMPTS[min(attempt, len(_ATTEMPTS) - 1)]
        try:
            response = ollama.Client(timeout=OLLAMA_TIMEOUT).chat(
                model=model,
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": f"Spoken to-do list:\n\n{transcript}{suffix}"},
                ],
                format=TaskList.model_json_schema(),
                options=options,
                keep_alive="30m",
            )
            return validate_tasks(TaskList.model_validate_json(response.message.content), transcript)
        except (ValidationError, json.JSONDecodeError) as e:
            last_error = e
            print(f"      Attempt {attempt + 1} returned bad or cut-off JSON, retrying...")
        except Exception as e:  # timeout, connection closed by Ollama, etc.
            last_error = e
            print(f"      Attempt {attempt + 1} failed ({type(e).__name__}), retrying with new settings...")
            time.sleep(2)
    if isinstance(last_error, (ValidationError, json.JSONDecodeError)):
        raise RuntimeError(f"Model failed to return valid tasks: {last_error}")
    raise last_error


def main():
    parser = argparse.ArgumentParser(description="Jarvis Day 1: voice -> tasks")
    parser.add_argument("audio", nargs="?", help="Path to audio file (mp3, m4a, wav...)")
    parser.add_argument("--text", help="Skip audio and use this text (handy for testing)")
    parser.add_argument("--model", default="gemma3:4b", help="Ollama model name")
    parser.add_argument("--whisper", default=DEFAULT_WHISPER, help="Whisper size: tiny.en/base.en/small.en/medium.en")
    parser.add_argument("--hint", default="", help='Words Whisper often mishears, e.g. "Professor Khan, quiz 2, Python"')
    args = parser.parse_args()

    if not args.audio and not args.text:
        parser.error("Provide an audio file or --text")

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    hint = args.hint
    hints_file = OUTPUT_DIR / "hints.txt"
    if not hint and hints_file.exists():
        hint = hints_file.read_text(encoding="utf-8-sig", errors="ignore").strip()
    if hint and not args.text:
        print(f"      Using hint words: {hint}")

    transcript = args.text if args.text else transcribe(args.audio, args.whisper, hint)
    (OUTPUT_DIR / "transcript.txt").write_text(transcript, encoding="utf-8")
    print(f"\nTranscript:\n{transcript}\n")

    try:
        tasks = extract_tasks(transcript, args.model)
    except Exception as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)

    out_file = OUTPUT_DIR / "tasks.json"
    out_file.write_text(tasks.model_dump_json(indent=2), encoding="utf-8")

    print(f"Found {len(tasks.tasks)} task(s):")
    for i, t in enumerate(tasks.tasks, 1):
        flag = "  [needs clarification]" if t.needs_clarification else ""
        if t.type == "reminder" and t.when:
            flag = f"  [at: {t.when}]" + flag
        print(f"  {i}. ({t.type}) {t.title}{flag}")
    print(f"\nSaved to {out_file}")


if __name__ == "__main__":
    main()