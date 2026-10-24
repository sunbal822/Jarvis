"""
Fill in the gold answers for eval_set_draft.json and merge them into eval_set.json.

Edit LABELS below if you disagree with any label. Each key is the time part of the voice-note
filename; the value is the list of tasks you MEANT (None = leave this note out of the eval).

Usage:  python apply_labels.py
"""

import json
from pathlib import Path

HERE = Path(__file__).parent
DRAFT = HERE / "eval_set_draft.json"
FINAL = HERE / "eval_set.json"


def t(kind, when="", clar=False):
    return {"type": kind, "when": when, "needs_clarification": clar}


LABELS = {
    "18.50.29": [t("code"), t("email")],                       # says "mail", but means email (hard case)
    "18.50.45": [t("reminder", "in five minutes")],            # Whisper heard "Sit a reminder"
    "18.50.54": [t("reminder", "around 7 pm")],
    "18.51.06": [t("file"), t("other")],                       # assignment document + short explanation
    "18.51.13": None,                                          # "what did we do today": the UI answers this from records
    "18.51.35": [t("message")],                                # "around 30 minutes" is NOT a reminder
    "18.51.47": [t("reminder", "8am tomorrow")],
    "18.52.06": [t("email")],                                  # "Friday" is NOT a reminder
    "18.52.22": [t("code")],
    "18.52.35": [t("code")],
    "18.52.49": [t("other")],                                  # Jarvis cannot read your sent emails: honest-failure case
    "18.53.13": [t("other")],                                  # says "summary" but it is NOT a chat summary
}


def main():
    draft = json.loads(DRAFT.read_text(encoding="utf-8"))
    existing = json.loads(FINAL.read_text(encoding="utf-8")) if FINAL.exists() else []
    seen = {c["transcript"] for c in existing}

    added = skipped = 0
    for entry in draft:
        key = next((k for k in LABELS if k in entry["source"]), None)
        gold = LABELS.get(key) if key else None
        if gold is None:
            skipped += 1
            print(f"skipped: {entry['source']}")
            continue
        if entry["transcript"] in seen:
            continue
        existing.append({"source": entry["source"], "transcript": entry["transcript"], "gold": gold})
        seen.add(entry["transcript"])
        added += 1

    FINAL.write_text(json.dumps(existing, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"Added {added}, skipped {skipped}. eval_set.json now has {len(existing)} cases.")


if __name__ == "__main__":
    main()