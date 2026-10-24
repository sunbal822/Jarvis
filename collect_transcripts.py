"""
Turn a folder of voice notes into a draft eval set (transcripts only, answers left empty).

Usage:
  python collect_transcripts.py C:\\path\\to\\my_notes
  python collect_transcripts.py C:\\path\\to\\my_notes --whisper small.en

Writes eval_set_draft.json next to this script. For each entry you fill in "gold",
then copy the finished entries into eval_set.json.

Uses the same Whisper settings as day1_voice_to_tasks.py, so the transcripts are exactly
what Jarvis would hear. Does NOT need Ollama.
"""

import argparse
import json
import os
from pathlib import Path

from faster_whisper import WhisperModel

HERE = Path(__file__).parent
AUDIO_EXT = {".ogg", ".wav", ".mp3", ".m4a", ".opus", ".flac"}
HINTS_FILE = Path.home() / "jarvis_output" / "hints.txt"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("folder", help="Folder containing your voice notes")
    parser.add_argument("--whisper", default=os.environ.get("JARVIS_WHISPER", "small.en"))
    args = parser.parse_args()

    files = sorted(p for p in Path(args.folder).iterdir() if p.suffix.lower() in AUDIO_EXT)
    if not files:
        raise SystemExit(f"No audio files found in {args.folder}")

    hint = HINTS_FILE.read_text(encoding="utf-8-sig", errors="ignore").strip() if HINTS_FILE.exists() else ""
    prompt = f"Vocabulary: {hint}." if hint else "A spoken to-do list for a personal assistant."
    lang = None if args.whisper.endswith(".en") else "en"

    print(f"Loading Whisper ({args.whisper}) once...")
    model = WhisperModel(args.whisper, device="auto", compute_type="int8")

    draft = []
    for i, path in enumerate(files, 1):
        segments, _ = model.transcribe(str(path), vad_filter=True, initial_prompt=prompt, language=lang,
                                       beam_size=5, condition_on_previous_text=False)
        text = " ".join(seg.text.strip() for seg in segments).strip()
        print(f"[{i}/{len(files)}] {path.name}: {text}")
        draft.append({
            "source": path.name,
            "transcript": text,
            "gold": [],  # <- YOU fill this in: [{"type": "...", "when": "", "needs_clarification": false}]
        })

    out = HERE / "eval_set_draft.json"
    out.write_text(json.dumps(draft, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\nSaved {len(draft)} transcripts to {out}")


if __name__ == "__main__":
    main()