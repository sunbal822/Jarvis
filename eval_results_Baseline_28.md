| model | valid_json | exact_task_list | type_precision | type_recall | reminder_time_correct | clarification_flag_correct | avg_seconds_per_case |
|---|---|---|---|---|---|---|---|
| gemma3:4b | 100% | 86% | 86% | 86% | 83% | 91% | 11.1 |

Exact task list, split by where the transcript came from (headline = real voice notes):

| model | real voice notes | synthetic |
|---|---|---|
| gemma3:4b | 17/21 = 81% | 7/7 = 100% |

## Misses (read these, they are your best material)
- gemma3:4b case 11: {"case": 11, "transcript": "I have an assignment on deep learning and its uses, compile an assignment for that and also give me a short summary so that I understand what's going on.", "gold": ["file", "other"], "predicted": [["code", ""], ["chat_summary", ""]]}
- gemma3:4b case 17: {"case": 17, "transcript": "Also, can you give me a summary of all the emails I have ever sent to Ali as of now?", "gold": ["other"], "predicted": [["chat_summary", ""]]}
- gemma3:4b case 21: {"case": 21, "transcript": "Set a reminder to call my mom.", "gold": ["reminder"], "predicted": [["reminder", "in 20 minutes"]]}
- gemma3:4b case 22: {"case": 22, "transcript": "Give me a summary of artificial intelligence and its uses.", "gold": ["other"], "predicted": [["chat_summary", ""]]}
- gemma3:4b case 23: {"case": 23, "transcript": "Okay so for today we have to firstly go over our assignment, the one with the calculator, the Python calculator. Secondly we have a meeting at 5, so we have to attend that. Also I have dinner at 8, a company dinner, so I need a reminder for for that as well.", "gold": ["code", "reminder", "reminder"], "predicted": [["other", ""], ["reminder", "5pm"], ["reminder", "8pm"]]}
- gemma3:4b case 24: {"case": 24, "transcript": "Remind me on Saturday that I need to go visit the dentist.", "gold": ["reminder"], "predicted": [["reminder", "Saturday at 00:00:00"]]}