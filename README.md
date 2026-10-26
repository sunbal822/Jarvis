# Jarvis: an overnight voice-to-tasks assistant that runs on your own laptop

Record a voice note before bed. Jarvis turns it into typed tasks, does them while you sleep, and gives you a morning report (and a spoken summary).

Jarvis turns a spoken to-do list into typed tasks and does them overnight. The listening and thinking (faster-whisper and Gemma 3 4B through Ollama) run entirely on my laptop. Nothing is uploaded except, optionally, the short text of the morning summary, which goes to ElevenLabs to be spoken. There are no API fees, so a night costs nothing beyond electricity.
Transcription (faster-whisper) and the thinking (Gemma 3 4B through Ollama) run locally. Nothing is uploaded. The spoken summary uses ElevenLabs and is optional.

## What it does

| Task type | What Jarvis does |
|---|---|
| code | Writes the program, runs it, and checks it doesn't crash |
| email | Drafts only. It never sends anything |
| message | Writes a short WhatsApp/SMS-style text |
| reminder | Parses the time with plain code and pops up when it is due |
| chat_summary | Summarizes a chat you exported into `~/jarvis_output/inputs/` |
| file | Creates a document or file |
| other | Questions, explanations, plans |

## Requirements

- Python 3.10+
- [Ollama](https://ollama.com), then: `ollama pull gemma3:4b`
- `pip install faster-whisper ollama pydantic`
- For the Jarvis window (`jarvis_ui.py`): [CHECK the imports at the top of jarvis_ui.py and list the packages here, e.g. vosk for the "Hey Jarvis" wake word, if you use it]
- For the spoken summary (optional): an ElevenLabs API key. Set it as the `ELEVENLABS_API_KEY` environment variable, or paste it in the app under Voice settings. The key is never stored in the code.

## Run it once

```
python run_overnight.py my_voice.ogg
```

Results are written to `~/jarvis_output/`:

- `transcript.txt`: what you said
- `tasks.json`: the typed tasks
- `morning_report.md`: what was done

Options: `--model gemma3:4b` and `--whisper small.en`.

## Run it every night (Windows)

1. Save your voice note as `tonight.ogg` in the Jarvis folder.
2. Open Task Scheduler (Win+R, type `taskschd.msc`).
3. Create Basic Task, name it "Jarvis nightly", trigger Daily at your time (for example 2:00 AM).
4. Action: Start a program, pick `run_nightly.bat`, and set "Start in" to the Jarvis folder.
5. In the task's Conditions tab, tick "Wake the computer to run this task". Keep the laptop plugged in.

`run_nightly.bat` logs to `nightly_log.txt` and moves `tonight.ogg` to `last_night.ogg` afterwards.

## How it stays reliable

The model judges, code guarantees:

- Reminder times are parsed by `jarvis_reminders.py`, never by the model.
- `validate_tasks` removes any reminder day or number the speaker did not say, and marks the task "needs clarification".
- A summary is only a chat summary if the transcript mentions a chat.
- If a retry is needed, the sampling settings change on every attempt, which breaks the repeat-loops a 4B model can fall into.

## Tests and evaluation

```
python test_reminders.py
python eval_extraction.py --models gemma3:4b --label my_run
```

`test_reminders.py` runs 18 parser checks. `eval_extraction.py` scores 28 hand-labelled spoken to-do lists (valid JSON, exact task list, reminder time, clarification flag).

| Run | Cases | Valid JSON | Exact task list | Reminder time | Clarification |
|---|---|---|---|---|---|
| Baseline | 28 | 100% | 86% | 83% | 91% |
| Current | 28 | 100% | 100% | 100% | 89% |

The rules were tuned on these same cases, so treat the numbers as "found and fixed", not as a held-out score.

## License

MIT. See the `LICENSE` file.
