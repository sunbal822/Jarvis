"""
Jarvis - desktop HUD + chat window (v6).

What is new in v6:
- New task types: "message" (a text/WhatsApp message, not an email) and "reminder" (reminders, calls, meetings).
- Reminders are saved and Jarvis pops up and speaks them at the right time. Jarvis knows your upcoming reminders.
- "What have we done so far?" and "what do I have scheduled?" are answered from real records, not guessed.
- Vague requests get one clear question instead of a dead-end reply. Jarvis never claims to have sent anything.

v5.3 (look only):

What is new in v5.3 (look only):
- Hero is now just the pixel field + hidden JARVIS (no stat boxes, no caption). The pixels sit still and react to
  the cursor (push, glow, parallax) and to clicks (shockwave ring through pixels and letters).
- Symmetric window outline, and the same 14px side margin on header, hero, bubbles, chips and input bar.


What is new in v5.2 (look only, behaviour unchanged):
- Living pixel background with a hidden pixel-art JARVIS (hover to reveal), Runs/Tasks count-up, organic window
  shape, white-sweep button hovers, fades and rises, and pixels sucked into the screen centre at launch.
- Conversation: chat, thanks, recaps, and stop/replace mid-task. Tasks only run when you ask for one.
- Finished tasks are silent: the logo shows DONE (or NEEDS YOU) until you say or click something.

What is new in v5.1:
- Dynamic View Toggle: Single prominent header button ("📜 History" / "💬 Back to Chat") that never gets clipped.
- Enhanced "Hey Jarvis" Wake Word: Open vocabulary phoneme recognition with automatic stream reconnection.
- Hands-Free Voice Detection: Automatically detects when you finish speaking (2s of silence) and runs tasks hands-free!
- Flexible ElevenLabs Key Loading: Reads from Windows Registry, environment, or in-app voice settings immediately.
- In-App Voice Settings (⚙️ Voice): Paste, test, and save your ElevenLabs key directly inside Jarvis with audio preview.
- Direct SAPI COM Fallback: Instant, reliable Windows speech synthesis with zero lingering processes.
- Live task checklist with ticking checkmarks, progress, and Cancel button.
- Global Hotkey (Ctrl+Alt+J) to toggle Jarvis from anywhere.
- Click floating logo HUD to talk immediately.
- Quick action suggestion chips.

Run:  python jarvis_ui.py
"""

import ctypes
import json
import math
import os
import queue
import random
import re
import subprocess
import sys
import threading
import time
import tkinter as tk
import urllib.error
import urllib.request
import wave
import winreg
from ctypes import wintypes
from datetime import datetime
from pathlib import Path
from tkinter import filedialog, messagebox

import jarvis_reminders as rem

try:
    import winsound
except ImportError:
    winsound = None

try:
    import numpy as np
except ImportError:
    np = None

try:
    import sounddevice as sd
    import vosk
    VOSK_AVAILABLE = True
except ImportError:
    VOSK_AVAILABLE = False

try:
    import comtypes.client
    COMTYPES_AVAILABLE = True
except ImportError:
    COMTYPES_AVAILABLE = False

HERE = Path(__file__).parent
OUT = Path.home() / "jarvis_output"
REPORT = OUT / "morning_report.md"
RESULTS = OUT / "results.json"
HISTORY_FILE = OUT / "history.json"
UI_LOG = OUT / "ui_last_run.log"
APP_LOG = OUT / "jarvis_app.log"
SPEECH_FILE = OUT / "speech.txt"
NARRATION_WAV = OUT / "narration.wav"
VOICE_NOTE = OUT / "voice_note.wav"
KEY_FILE = OUT / "elevenlabs_key.txt"
VOSK_MODEL_PATH = Path.home() / ".cache" / "vosk" / "vosk-model-small-en-us-0.15"
NOWIN = getattr(subprocess, "CREATE_NO_WINDOW", 0)

PREFERRED_MODELS = ["gemma3:4b", "gemma3:12b"]  # 4B first: much faster on a laptop without a strong GPU
WHISPER_MODEL = os.environ.get("JARVIS_WHISPER", "small.en")  # tiny.en < base.en < small.en < medium.en
DEFAULT_VOICE_ID = "21m00Tcm4TlvDq8ikWAM"
ELEVEN_MODEL = "eleven_multilingual_v2"

# ---- look of the chat window ----
KEYCOL = "#010101"
BG, PANEL, BUBBLE = "#0e1116", "#151922", "#1c212c"
USER_BG, TEXT, MUTED = "#2f6fed", "#e8eaf0", "#8b93a5"
GOOD, WARN, BAD, ACCENT = "#4cc38a", "#f2b84b", "#ef6461", "#5aa2ff"
FONT = ("Segoe UI", 10)
CARD = "#232a38"

# ---- look and timing of the logo ----
CYAN, CYAN2, BLUE, DEEP = "#3de6ff", "#18b8e0", "#0f86bd", "#0a5a85"
DIM, YEL, WHITE, SHADOW = "#0c4766", "#f7c92b", "#f2fbff", "#021a26"
INTRO_R = 150
DOCK_R = 72
PAD = 14
TEXT_H = 64
GROW_S, DOCK_S = 1.4, 1.1
DOCK_MARGIN_X, DOCK_MARGIN_Y = 28, 72
PANEL_W, PANEL_H = 400, 550
GREETING = "Hey! How are you doing today?"
GREET_ALOUD = True
NB = 32
NBARS = 64
SPEEDS = (-40.0, 55.0, -130.0)
BAR_BAND = [min(NB - 1, int(abs(i / NBARS * 2 - 1) * NB)) for i in range(NBARS)]

_OLLAMA_PROC = None


class UserError(Exception):
    """An error whose message is safe and friendly to show."""


def log_ui(msg):
    line = f"[{datetime.now().strftime('%H:%M:%S')}] {msg}\n"
    try:
        OUT.mkdir(parents=True, exist_ok=True)
        with APP_LOG.open("a", encoding="utf-8") as f:
            f.write(line)
        with UI_LOG.open("a", encoding="utf-8") as f:
            f.write(line)
    except OSError:
        pass


# ---------- ElevenLabs key management ----------
def get_elevenlabs_key():
    """Dynamically resolves key from key file, Windows registry (setx), or os.environ."""
    # 1. Direct file in jarvis_output
    if KEY_FILE.exists():
        try:
            k = KEY_FILE.read_text(encoding="utf-8").strip().strip('"').strip("'")
            if k and not k.lower().startswith("paste-") and len(k) > 10:
                return k
        except Exception:
            pass

    # 2. Windows Registry HKCU\Environment (where `setx` writes immediately)
    if sys.platform == "win32":
        try:
            reg_key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Environment")
            val, _ = winreg.QueryValueEx(reg_key, "ELEVENLABS_API_KEY")
            v = val.strip().strip('"').strip("'")
            if v and not v.lower().startswith("paste-") and len(v) > 10:
                return v
        except Exception:
            pass

    # 3. Environment variable
    raw = os.environ.get("ELEVENLABS_API_KEY", "").strip().strip('"').strip("'")
    if raw and not raw.lower().startswith("paste-") and len(raw) > 10:
        return raw

    return ""


def save_elevenlabs_key(key_str):
    """Saves API key to key_file and registry so it takes effect instantly everywhere."""
    key_clean = key_str.strip().strip('"').strip("'")
    OUT.mkdir(parents=True, exist_ok=True)
    KEY_FILE.write_text(key_clean, encoding="utf-8")
    os.environ["ELEVENLABS_API_KEY"] = key_clean
    if sys.platform == "win32":
        try:
            reg_key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Environment", 0, winreg.KEY_SET_VALUE)
            winreg.SetValueEx(reg_key, "ELEVENLABS_API_KEY", 0, winreg.REG_SZ, key_clean)
        except Exception as e:
            log_ui(f"could not write registry key: {e}")


# ---------- history helpers ----------
def load_history():
    if not HISTORY_FILE.exists():
        if RESULTS.exists():
            try:
                res = json.loads(RESULTS.read_text(encoding="utf-8"))
                mtime = RESULTS.stat().st_mtime
                ts = datetime.fromtimestamp(mtime).strftime("%Y-%m-%d %H:%M")
                spoken, _ = summarize(res)
                entry = {
                    "id": str(int(mtime)),
                    "timestamp": ts,
                    "mode": "recent",
                    "prompt": "Previous run",
                    "summary": spoken,
                    "results": res,
                    "report_exists": REPORT.exists(),
                }
                save_history([entry])
                return [entry]
            except Exception:
                return []
        return []
    try:
        return json.loads(HISTORY_FILE.read_text(encoding="utf-8"))
    except Exception:
        return []


def save_history(entries):
    try:
        OUT.mkdir(parents=True, exist_ok=True)
        HISTORY_FILE.write_text(json.dumps(entries[:60], indent=2), encoding="utf-8")
    except Exception as e:
        log_ui(f"failed to save history: {e}")


def add_history_entry(mode, prompt, summary, results):
    entries = load_history()
    entry = {
        "id": str(int(time.time())),
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "mode": mode,
        "prompt": str(prompt)[:300] if prompt else "Voice command",
        "summary": summary,
        "results": results or [],
        "report_exists": REPORT.exists(),
    }
    entries.insert(0, entry)
    save_history(entries)


# ---------- small maths helpers ----------
def lerp(a, b, t):
    return a + (b - a) * t


def ease_out(t):
    return 1 - (1 - t) ** 3


def ease_in_out(t):
    return 3 * t * t - 2 * t * t * t


def mix(a, b, t):
    t = max(0.0, min(1.0, t))
    ca = [int(a[i:i + 2], 16) for i in (1, 3, 5)]
    cb = [int(b[i:i + 2], 16) for i in (1, 3, 5)]
    return "#%02x%02x%02x" % tuple(int(x + (y - x) * t) for x, y in zip(ca, cb))


# ---------- voice analysis ----------
class Meter:
    N = 1024

    def __init__(self, rate):
        self.win = np.hanning(self.N).astype(np.float32)
        freqs = np.fft.rfftfreq(self.N, 1.0 / rate)
        edges = np.geomspace(90, min(5500, rate * 0.45), NB + 1)
        self.idx = []
        for i in range(NB):
            lo = int(np.searchsorted(freqs, edges[i]))
            hi = max(int(np.searchsorted(freqs, edges[i + 1])), lo + 1)
            self.idx.append((lo, hi))
        self.peak = 0.5

    def bands(self, samples):
        n = self.N
        if len(samples) < n:
            samples = np.pad(samples, (n - len(samples), 0))
        mag = np.abs(np.fft.rfft(samples[-n:] * self.win)) / (n * 0.25)
        db = 20.0 * np.log10(mag + 1e-7)
        norm = np.clip((db + 68.0) / 40.0, 0.0, 1.0)
        vals = [float(norm[lo:hi].max()) for lo, hi in self.idx]
        self.peak = max(max(vals), self.peak * 0.995)
        gain = 0.9 / max(self.peak, 0.5)
        return [min(1.0, max(0.0, v * gain - 0.12) / 0.88) for v in vals]


def speech_bands(t):
    env = max(0.0, math.sin(t * 8.5)) ** 1.5 * (0.6 + 0.4 * math.sin(t * 2.3))
    c = (0.35 + 0.3 * math.sin(t * 1.7)) * NB
    return [min(1.0, env * 0.95 * math.exp(-(((b - c) / (NB * 0.18)) ** 2)) + 0.04 * env)
            for b in range(NB)]


def boot_bands(t, ramp):
    return [ramp * max(0.0, math.sin(t * 5 - b * 0.35)) ** 1.5 * 0.9 for b in range(NB)]


# ---------- microphone with live levels ----------
class WAVEFORMATEX(ctypes.Structure):
    _fields_ = [("wFormatTag", ctypes.c_ushort), ("nChannels", ctypes.c_ushort),
                ("nSamplesPerSec", ctypes.c_uint), ("nAvgBytesPerSec", ctypes.c_uint),
                ("nBlockAlign", ctypes.c_ushort), ("wBitsPerSample", ctypes.c_ushort),
                ("cbSize", ctypes.c_ushort)]


class WAVEHDR(ctypes.Structure):
    _fields_ = [("lpData", ctypes.c_void_p), ("dwBufferLength", ctypes.c_uint),
                ("dwBytesRecorded", ctypes.c_uint), ("dwUser", ctypes.c_size_t),
                ("dwFlags", ctypes.c_uint), ("dwLoops", ctypes.c_uint),
                ("lpNext", ctypes.c_void_p), ("reserved", ctypes.c_size_t)]


class MicRecorder:
    RATE, CHUNK, NBUF = 16000, 640, 8
    WHDR_DONE = 1

    def __init__(self):
        if sys.platform != "win32" or np is None:
            raise RuntimeError("live recorder needs Windows and numpy")
        self.winmm = ctypes.windll.winmm
        self.frames = []
        self.tail = np.zeros(0, dtype=np.float32)
        self.bands = [0.0] * NB
        self.meter = Meter(self.RATE)
        self.running = False
        self.h = ctypes.c_void_p()
        self.hdrs, self.bufs, self.cur = [], [], 0
        self.opened = False

    def _check(self, code, what):
        if code:
            raise RuntimeError(f"{what} failed (code {code})")

    def start(self):
        w = self.winmm
        hp, pp = ctypes.c_void_p, ctypes.POINTER(WAVEHDR)
        w.waveInOpen.argtypes = [ctypes.POINTER(ctypes.c_void_p), ctypes.c_uint,
                                 ctypes.POINTER(WAVEFORMATEX), ctypes.c_size_t,
                                 ctypes.c_size_t, ctypes.c_uint]
        for name in ("waveInPrepareHeader", "waveInAddBuffer", "waveInUnprepareHeader"):
            getattr(w, name).argtypes = [hp, pp, ctypes.c_uint]
        for name in ("waveInStart", "waveInStop", "waveInReset", "waveInClose"):
            getattr(w, name).argtypes = [hp]
        try:
            fmt = WAVEFORMATEX(1, 1, self.RATE, self.RATE * 2, 2, 16, 0)
            self._check(w.waveInOpen(ctypes.byref(self.h), 0xFFFFFFFF, ctypes.byref(fmt), 0, 0, 0),
                        "waveInOpen")
            self.opened = True
            size = self.CHUNK * 2
            for _ in range(self.NBUF):
                buf = ctypes.create_string_buffer(size)
                hdr = WAVEHDR()
                hdr.lpData = ctypes.cast(buf, ctypes.c_void_p).value
                hdr.dwBufferLength = size
                self._check(w.waveInPrepareHeader(self.h, ctypes.byref(hdr), ctypes.sizeof(hdr)),
                            "prepare")
                self._check(w.waveInAddBuffer(self.h, ctypes.byref(hdr), ctypes.sizeof(hdr)), "add")
                self.bufs.append(buf)
                self.hdrs.append(hdr)
            self._check(w.waveInStart(self.h), "waveInStart")
        except Exception:
            self._close()
            raise
        self.running = True
        threading.Thread(target=self._loop, daemon=True).start()

    def _consume(self, hdr):
        n = hdr.dwBytesRecorded
        if n:
            data = ctypes.string_at(hdr.lpData, n)
            self.frames.append(data)
            x = np.frombuffer(data, dtype=np.int16).astype(np.float32) / 32768.0
            self.tail = np.concatenate([self.tail, x])[-Meter.N:]
            self.bands = self.meter.bands(self.tail)

    def _loop(self):
        w = self.winmm
        while self.running:
            hdr = self.hdrs[self.cur]
            if hdr.dwFlags & self.WHDR_DONE:
                self._consume(hdr)
                hdr.dwBytesRecorded = 0
                w.waveInAddBuffer(self.h, ctypes.byref(hdr), ctypes.sizeof(hdr))
                self.cur = (self.cur + 1) % self.NBUF
            else:
                time.sleep(0.008)

    def _close(self):
        w = self.winmm
        if self.opened:
            w.waveInReset(self.h)
            for hdr in self.hdrs:
                w.waveInUnprepareHeader(self.h, ctypes.byref(hdr), ctypes.sizeof(hdr))
            w.waveInClose(self.h)
            self.opened = False

    def stop(self, path):
        self.running = False
        time.sleep(0.06)
        self.winmm.waveInStop(self.h)
        self.winmm.waveInReset(self.h)
        for _ in range(self.NBUF):
            hdr = self.hdrs[self.cur]
            if hdr.dwFlags & self.WHDR_DONE:
                self._consume(hdr)
            self.cur = (self.cur + 1) % self.NBUF
        self._close()
        path = Path(path)
        if path.exists():
            path.unlink()
        with wave.open(str(path), "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(self.RATE)
            wf.writeframes(b"".join(self.frames))
        return path

    def cancel(self):
        self.running = False
        time.sleep(0.06)
        try:
            self.winmm.waveInStop(self.h)
        except Exception:
            pass
        self._close()


# ---------- fallback microphone ----------
def mci(cmd, ignore=False):
    winmm = ctypes.windll.winmm
    buf = ctypes.create_unicode_buffer(255)
    err = winmm.mciSendStringW(cmd, buf, 254, 0)
    if err and not ignore:
        eb = ctypes.create_unicode_buffer(255)
        winmm.mciGetErrorStringW(err, eb, 254)
        raise RuntimeError(f"{cmd} -> {eb.value}")
    return buf.value


def rec_start():
    OUT.mkdir(parents=True, exist_ok=True)
    mci("close rec", ignore=True)
    try:
        mci("open new type waveaudio alias rec")
        mci("set rec alignment 2 bitspersample 16 samplespersec 16000 channels 1 bytespersec 32000")
        mci("record rec")
        return
    except RuntimeError:
        mci("close rec", ignore=True)
    mci("open new type waveaudio alias rec")
    mci("record rec")


def rec_stop():
    mci("stop rec")
    if VOICE_NOTE.exists():
        VOICE_NOTE.unlink()
    mci(f'save rec "{VOICE_NOTE}"')
    mci("close rec")
    return VOICE_NOTE


# ---------- drawing: the Jarvis HUD ----------
def draw_hud(c, cx, cy, R, rot, bars, text=True):
    def ring(r, w, col, start=0.0, extent=359.9):
        c.create_arc(cx - r * R, cy - r * R, cx + r * R, cy + r * R, start=start,
                     extent=extent, style="arc", outline=col, width=w)

    def circle(r, w, col, **kw):
        c.create_oval(cx - r * R, cy - r * R, cx + r * R, cy + r * R, outline=col, width=w, **kw)

    def at(r, deg):
        a = math.radians(deg)
        return cx + r * R * math.cos(a), cy - r * R * math.sin(a)

    lw = max(1.0, R * 0.02)

    for s, e in ((0, 75), (95, 40), (150, 90), (255, 30), (300, 45)):
        ring(0.975, lw * 1.6, CYAN, rot[0] + s, e)
    circle(0.915, 1, DIM)

    bw = max(1.5, R * 0.022)
    n = len(bars)
    for i, v in enumerate(bars):
        a = math.radians(90 - i * 360.0 / n)
        idle = 0.04 + (0.03 if i % 4 == 0 else 0.0)
        r2 = 0.745 + idle + 0.095 * v
        col = mix("#12648f", CYAN, 0.2 + 0.8 * v)
        c.create_line(cx + 0.745 * R * math.cos(a), cy - 0.745 * R * math.sin(a),
                      cx + r2 * R * math.cos(a), cy - r2 * R * math.sin(a), fill=col, width=bw)

    ring(0.715, max(2.0, R * 0.03), YEL, rot[2], 55)
    circle(0.69, 1, DIM)

    ring(0.60, R * 0.12, BLUE, rot[1] + 20, 215)
    ring(0.60, R * 0.12, DEEP, rot[1] + 235, 45)
    ring(0.668, max(1.5, R * 0.014), CYAN, rot[1] + 20, 215)
    ring(0.532, 1, CYAN2, rot[1] + 20, 215)
    d = max(1.5, R * 0.017)
    for k in range(5):
        x, y = at(0.60, rot[1] + 20 + 215 - 28 - k * 11)
        c.create_oval(x - d, y - d, x + d, y + d, fill=YEL, outline="")

    circle(0.50, 1, DIM, dash=(2, 4))
    circle(0.465, R * 0.07, "#08334a")
    circle(0.465, max(2.0, R * 0.04), CYAN)

    if text:
        f = ("Segoe UI", -max(7, int(R * 0.122)), "bold")
        c.create_text(cx + 1, cy + 1, text="J.A.R.V.I.S.", font=f, fill=SHADOW)
        c.create_text(cx, cy, text="J.A.R.V.I.S.", font=f, fill=WHITE)


def round_rect(c, x1, y1, x2, y2, r, **kw):
    pts = [x1 + r, y1, x2 - r, y1, x2, y1, x2, y1 + r, x2, y2 - r, x2, y2,
           x2 - r, y2, x1 + r, y2, x1, y2, x1, y2 - r, x1, y1 + r, x1, y1]
    return c.create_polygon(pts, smooth=True, **kw)


class IconButton(tk.Canvas):
    def __init__(self, parent, size, fill, icon, command, parent_bg):
        super().__init__(parent, width=size, height=size, bg=parent_bg,
                         highlightthickness=0, cursor="hand2")
        self.size, self.fill, self.icon, self.command = size, fill, icon, command
        self.enabled = True
        self.hk, self.h_target, self.h_job = 0.0, 0.0, None
        self.bind("<Button-1>", lambda e: self.command() if self.enabled else None)
        self.bind("<Enter>", lambda e: self.hover(1.0))
        self.bind("<Leave>", lambda e: self.hover(0.0))
        self.render()

    def hover(self, target):
        self.h_target = target
        if self.h_job is None:
            self.h_step()

    def h_step(self):
        self.h_job = None
        try:
            self.hk += (self.h_target - self.hk) * 0.4
            if abs(self.hk - self.h_target) < 0.02:
                self.hk = self.h_target
            self.render()
            if self.hk != self.h_target:
                self.h_job = self.after(16, self.h_step)
        except tk.TclError:
            pass

    def set(self, icon=None, fill=None):
        if icon:
            self.icon = icon
        if fill:
            self.fill = fill
        self.render()

    def render(self):
        self.delete("all")
        s = self.size
        c = s / 2
        fill = self.fill
        w = "#ffffff"
        if self.hk > 0 and isinstance(self.fill, str) and self.fill.startswith("#") and len(self.fill) == 7:
            fill = mix(self.fill, "#f2fbff", self.hk)
            w = mix("#ffffff", "#0e1116", self.hk)
        self.create_oval(1, 1, s - 1, s - 1, fill=fill, outline="")
        if self.icon == "send":
            self.create_polygon(c - 6, c - 7, c + 8, c, c - 6, c + 7, c - 3, c, fill=w, outline="")
        elif self.icon == "stop":
            self.create_rectangle(c - 5, c - 5, c + 5, c + 5, fill=w, outline="")
        elif self.icon == "plus":
            self.create_line(c - 6, c, c + 6, c, fill=w, width=2)
            self.create_line(c, c - 6, c, c + 6, fill=w, width=2)
        elif self.icon == "mic":
            self.create_oval(c - 4, c - 9, c + 4, c - 1, fill=w, outline="")
            self.create_rectangle(c - 4, c - 5, c + 4, c + 1, fill=w, outline="")
            self.create_oval(c - 4, c - 3, c + 4, c + 5, fill=w, outline="")
            self.create_arc(c - 8, c - 7, c + 8, c + 9, start=200, extent=140, style="arc",
                            outline=w, width=2)
            self.create_line(c, c + 9, c, c + 12, fill=w, width=2)
            self.create_line(c - 4, c + 12, c + 4, c + 12, fill=w, width=2)


# ======================================================================
# v5.2 look-only effects. Nothing below changes what Jarvis does.
# ======================================================================
HOVER_BG, HOVER_FG = "#f2fbff", "#0e1116"
_FX = {}


def _rgb(h):
    return (int(h[1:3], 16), int(h[3:5], 16), int(h[5:7], 16))


def _hex(c):
    return "#%02x%02x%02x" % tuple(max(0, min(255, int(v))) for v in c)


def _mixc(a, b, t):
    t = max(0.0, min(1.0, t))
    return (a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t, a[2] + (b[2] - a[2]) * t)


def _is_hex(s):
    return isinstance(s, str) and len(s) == 7 and s[0] == "#"


def _clamp01(v):
    return 0.0 if v < 0.0 else 1.0 if v > 1.0 else v


# ---------- every tk.Button: colours sweep to white on hover ----------
def install_button_hover(root):
    HB, HF = _rgb(HOVER_BG), _rgb(HOVER_FG)

    def sync(w, st):
        cur = (str(w.cget("bg")), str(w.cget("fg")))
        if cur != st.get("last"):
            if _is_hex(cur[0]) and _is_hex(cur[1]):
                st.update(ok=True, bg=_rgb(cur[0]), fg=_rgb(cur[1]), k=0.0, last=cur)
            else:
                st["ok"] = False

    def run(w, st):
        if st.get("job"):
            return

        def step():
            st["job"] = None
            try:
                if not w.winfo_exists():
                    _FX.pop(str(w), None)
                    return
                k = _clamp01(st["k"] + (0.2 if st["target"] else -0.2))
                st["k"] = k
                bg, fg = _hex(_mixc(st["bg"], HB, k)), _hex(_mixc(st["fg"], HF, k))
                w.config(bg=bg, fg=fg)
                st["last"] = (bg, fg)
                if k == 0.0:
                    w.config(activebackground=st["abg"], activeforeground=st["afg"])
                if (st["target"] and k < 1.0) or (not st["target"] and k > 0.0):
                    st["job"] = w.after(16, step)
            except tk.TclError:
                pass

        st["job"] = w.after(0, step)

    def enter(e):
        w = e.widget
        if not isinstance(w, tk.Button) or getattr(w, "_fx_lock", False):
            return
        try:
            if str(w.cget("state")) == "disabled":
                return
            st = _FX.setdefault(str(w), {"k": 0.0, "target": False})
            sync(w, st)
            if not st.get("ok"):
                return
            if st["k"] == 0.0 and not st.get("job"):
                st["abg"], st["afg"] = str(w.cget("activebackground")), str(w.cget("activeforeground"))
            st["target"] = True
            w.config(activebackground=HOVER_BG, activeforeground=HOVER_FG)
            run(w, st)
        except tk.TclError:
            pass

    def leave(e):
        w = e.widget
        st = _FX.get(str(w))
        if not isinstance(w, tk.Button) or not st or not st.get("ok"):
            return
        try:
            sync(w, st)
            st["target"] = False
            run(w, st)
        except tk.TclError:
            pass

    root.bind_class("Button", "<Enter>", enter, add="+")
    root.bind_class("Button", "<Leave>", leave, add="+")


def fade_in_button(btn, delay=0.0, dur=0.5):
    """Fade a freshly built button in from the panel background."""
    try:
        bg, fg = str(btn.cget("bg")), str(btn.cget("fg"))
        if not (_is_hex(bg) and _is_hex(fg)):
            return
        b1, f1, z = _rgb(bg), _rgb(fg), _rgb(BG)
        t0 = time.time() + delay
        btn._fx_lock = True
        btn.config(bg=BG, fg=BG)

        def step():
            try:
                if not btn.winfo_exists():
                    return
                p = (time.time() - t0) / dur
                if p >= 1.0:
                    btn.config(bg=bg, fg=fg)
                    btn._fx_lock = False
                    return
                k = ease_out(_clamp01(p))
                btn.config(bg=_hex(_mixc(z, b1, k)), fg=_hex(_mixc(z, f1, k)))
                btn.after(16, step)
            except tk.TclError:
                pass

        btn.after(16, step)
    except tk.TclError:
        pass


# ---------- soft, breathing, organic window outline (Windows) ----------
class OrganicShape:
    def __init__(self, win):
        self.win, self.t0 = win, time.time()
        self.ok = sys.platform == "win32"
        if self.ok:
            try:
                self.u32 = ctypes.WinDLL("user32", use_last_error=True)
                self.g32 = ctypes.WinDLL("gdi32", use_last_error=True)
                self.u32.GetParent.argtypes = [wintypes.HWND]
                self.u32.GetParent.restype = wintypes.HWND
                self.u32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
                self.u32.SetWindowRgn.argtypes = [wintypes.HWND, ctypes.c_void_p, wintypes.BOOL]
                self.g32.CreatePolygonRgn.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_int]
                self.g32.CreatePolygonRgn.restype = ctypes.c_void_p
            except Exception:
                self.ok = False

    def start(self):
        if self.ok:
            self.win.after(120, self.tick)

    def tick(self):
        try:
            if not self.win.winfo_exists():
                return
            if self.win.state() == "normal":
                self.apply()
            self.win.after(200, self.tick)
        except tk.TclError:
            pass
        except Exception as e:
            log_ui(f"organic shape off: {e}")

    def outline(self, w, h, t=0.0):
        # Straight left/right/top/bottom edges (nothing is clipped off the sides) with identical
        # rounded corners left and right. Top corners stay tight so the title-bar buttons are never clipped.
        rt, rb = 10, 28
        pts = []
        for ang in range(180, 271, 15):          # top-left
            r = math.radians(ang)
            pts.append((rt + rt * math.cos(r), rt + rt * math.sin(r)))
        for ang in range(270, 361, 15):          # top-right
            r = math.radians(ang)
            pts.append((w - rt + rt * math.cos(r), rt + rt * math.sin(r)))
        for ang in range(0, 91, 10):             # bottom-right
            r = math.radians(ang)
            pts.append((w - rb + rb * math.cos(r), h - rb + rb * math.sin(r)))
        for ang in range(90, 181, 10):           # bottom-left
            r = math.radians(ang)
            pts.append((rb + rb * math.cos(r), h - rb + rb * math.sin(r)))
        return pts

    def apply(self):
        hwnd = self.u32.GetParent(self.win.winfo_id()) or self.win.winfo_id()
        rect = wintypes.RECT()
        if not self.u32.GetWindowRect(hwnd, ctypes.byref(rect)):
            return
        w, h = rect.right - rect.left, rect.bottom - rect.top
        if w < 50 or h < 50:
            return
        if getattr(self, "_last_size", None) == (w, h):
            return
        self._last_size = (w, h)
        pts = self.outline(w, h)
        arr = (wintypes.POINT * len(pts))(*[(int(x), int(y)) for x, y in pts])
        rgn = self.g32.CreatePolygonRgn(ctypes.byref(arr), len(pts), 1)
        if rgn:
            self.u32.SetWindowRgn(hwnd, rgn, True)


# ---------- small square pixels sucked toward the middle of the screen ----------
class PixelVortex:
    COLS = (CYAN, CYAN2, WHITE, CYAN, YEL)

    def __init__(self, root, sw, sh, center, n=120):
        self.sw, self.sh, (self.cx, self.cy) = sw, sh, center
        self.n, self.spawning, self.parts = n, True, []
        self.top = tk.Toplevel(root)
        self.top.overrideredirect(True)
        self.top.attributes("-topmost", True)
        self.top.configure(bg=KEYCOL)
        self.top.attributes("-transparentcolor", KEYCOL)
        self.top.geometry(f"{sw}x{sh}+0+0")
        self.c = tk.Canvas(self.top, bg=KEYCOL, highlightthickness=0)
        self.c.pack(fill="both", expand=True)
        self.top.update_idletasks()
        self.click_through()

    def click_through(self):
        try:
            u = ctypes.WinDLL("user32")
            u.GetParent.argtypes = [wintypes.HWND]
            u.GetParent.restype = wintypes.HWND
            hwnd = u.GetParent(self.top.winfo_id()) or self.top.winfo_id()
            style = u.GetWindowLongW(wintypes.HWND(hwnd), -20)
            u.SetWindowLongW(wintypes.HWND(hwnd), -20, style | 0x80000 | 0x20 | 0x80 | 0x08000000)
        except Exception:
            pass

    def spawn(self):
        a = random.uniform(0, math.tau)
        d = random.uniform(0.25, 0.62) * max(self.sw, self.sh)
        s = random.choice((2, 3, 3, 4, 5))
        col = random.choice(self.COLS)
        item = self.c.create_rectangle(-9, -9, -8, -8, fill=col, outline="")
        self.parts.append([a, d, random.uniform(20, 90), s, item])

    def step(self, dt):
        """Returns False once everything has been sucked in and the overlay is gone."""
        try:
            if self.spawning:
                for _ in range(4):
                    if len(self.parts) < self.n:
                        self.spawn()
            for p in self.parts[:]:
                a, d, v, s, item = p
                v += (350 + 2600 * (1 - min(1.0, d / 800.0))) * dt
                d -= v * dt
                a += (260.0 / (d + 90.0)) * dt * 2.4
                if d < 16:
                    self.c.delete(item)
                    self.parts.remove(p)
                    continue
                p[0], p[1], p[2] = a, d, v
                x, y = self.cx + math.cos(a) * d, self.cy + math.sin(a) * d
                r = s * (0.35 + 0.65 * min(1.0, d / 170.0)) / 2
                self.c.coords(item, x - r, y - r, x + r, y + r)
            if not self.spawning and not self.parts:
                self.top.destroy()
                return False
            return True
        except tk.TclError:
            return False


# ---------- hero: a living pixel field, a hidden pixel-art J.A.R.V.I.S., and the run counters ----------
_FONT5 = {
    "J": ("00111", "00010", "00010", "00010", "00010", "10010", "01100"),
    "A": ("01110", "10001", "10001", "11111", "10001", "10001", "10001"),
    "R": ("11110", "10001", "10001", "11110", "10100", "10010", "10001"),
    "V": ("10001", "10001", "10001", "10001", "10001", "01010", "00100"),
    "I": ("11111", "00100", "00100", "00100", "00100", "00100", "11111"),
    "S": ("01111", "10000", "10000", "01110", "00001", "00001", "11110"),
}


class HeroField(tk.Canvas):
    """A calm pixel field with a hidden pixel-art JARVIS. Everything is still until you touch it:
    the cursor pushes and lights up nearby pixels (they spring back), the word shows under the
    cursor, deeper pixels shift a little with the cursor (parallax), and a click sends a shockwave
    ring through the pixels and the letters."""
    N, RAD, GLOW_R, PUSH_R = 170, 84, 64, 56

    def __init__(self, parent, height=132):
        super().__init__(parent, width=10, height=height, bg=BG, highlightthickness=0)
        self.H, self.W, self.cs = height, 0, 8
        rnd = random.Random(11)
        self.pix = []
        for i in range(self.N):
            depth = rnd.random()
            base = _rgb(YEL) if i % 17 == 0 else _mixc(_rgb(DEEP), _rgb(CYAN), depth)
            # [home_x, home_y, off_x, off_y, vel_x, vel_y, depth, glow, item, intro_angle, last_fill, base_rgb]
            self.pix.append([rnd.uniform(0, 380), rnd.uniform(0, height), 0.0, 0.0, 0.0, 0.0, depth, 0.0,
                             None, rnd.uniform(0, 6.28), "", base])
        self.cells = []
        self.mouse = self.smooth = None
        self.par = (0.0, 0.0)
        self.ripples = []
        self.moving = True
        self.t_on = None
        self.last = time.time()
        self.bind("<Configure>", self.build)
        self.bind("<Motion>", self.on_move)
        self.bind("<Button-1>", self.on_click)
        self.bind("<Leave>", self.on_leave)

    def on_move(self, e):
        self.mouse = (e.x, e.y)
        if self.smooth is None:
            self.smooth = (e.x, e.y)

    def on_leave(self, _e):
        self.mouse = self.smooth = None

    def on_click(self, e):
        self.ripples.append((e.x, e.y, time.time()))
        self.ripples = self.ripples[-4:]

    def restart(self, t):
        self.t_on = t
        self.moving = True
        for p in self.pix:
            p[10] = ""

    def build(self, e):
        if abs(e.width - self.W) < 2:
            return
        old = self.W
        self.W = e.width
        if old:
            for p in self.pix:
                p[0] *= self.W / old
        else:
            for p in self.pix:
                p[0] = p[0] / 380.0 * self.W
        self.delete("all")
        for p in self.pix:
            p[8], p[10] = self.create_rectangle(-9, -9, -8, -8, fill=BG, outline=""), ""
        self.cs = max(4, min(9, int((self.W - 40) // 35)))
        cs = self.cs
        gx, gy = (self.W - 35 * cs) / 2, (self.H - 7 * cs) / 2
        self.cells = []
        for li, ch in enumerate("JARVIS"):
            for row, bits in enumerate(_FONT5[ch]):
                for col, b in enumerate(bits):
                    if b == "1":
                        idx = li * 6 + col
                        x, y = gx + idx * cs, gy + row * cs
                        item = self.create_rectangle(x, y, x + cs - 1, y + cs - 1, fill=BG,
                                                     outline="", state="hidden")
                        self.cells.append([item, x + cs / 2, y + cs / 2, 0.0, False, idx / 35.0])
        self.moving = True

    def tick(self, now):
        if self.t_on is None or not self.cells:
            return
        dt = min(0.05, max(0.001, now - self.last))
        self.last = now
        a = ease_out(_clamp01((now - self.t_on) / 0.7))
        intro = (now - self.t_on) < 2.2
        W, H, m = self.W, self.H, self.mouse
        if m is not None and self.smooth is not None:
            self.smooth = (self.smooth[0] + (m[0] - self.smooth[0]) * 0.4,
                           self.smooth[1] + (m[1] - self.smooth[1]) * 0.4)
        sm = self.smooth if m is not None else None
        bgc = _rgb(BG)
        c_cyan, c_white = _rgb(CYAN), _rgb(WHITE)
        self.ripples = [r for r in self.ripples if now - r[2] < 1.3]

        # gentle parallax: deeper pixels lean toward the cursor
        tx, ty = ((m[0] - W / 2) / max(1.0, W) * 16, (m[1] - H / 2) / max(1.0, H) * 10) if m else (0.0, 0.0)
        k_par = min(1.0, dt * 5)
        px, py = self.par[0] + (tx - self.par[0]) * k_par, self.par[1] + (ty - self.par[1]) * k_par
        self.par = (px, py)

        # background: still pixels that react to the cursor, clicks and each other's springs
        busy = intro or sm is not None or bool(self.ripples) or self.moving or abs(px) > 0.03 or abs(py) > 0.03
        if busy:
            asm = _clamp01((now - self.t_on - 0.05) / 1.8)
            moving = False
            damp = max(0.0, 1.0 - dt * 9.0)
            for p in self.pix:
                hx, hy, ox, oy, vx, vy, depth, g, item, fa, last, base = p
                x, y = hx + ox, hy + oy
                fx = fy = tg = 0.0
                if sm is not None:
                    dx, dy = x - sm[0], y - sm[1]
                    d = math.hypot(dx, dy)
                    if d < self.GLOW_R:
                        t = 1 - d / self.GLOW_R
                        tg = t * t
                    if 0.1 < d < self.PUSH_R:
                        f = (1 - d / self.PUSH_R) ** 2 * 900.0
                        fx += dx / d * f
                        fy += dy / d * f
                for rx, ry, rt in self.ripples:
                    age = now - rt
                    dx, dy = x - rx, y - ry
                    d = math.hypot(dx, dy)
                    if d > 0.1:
                        ring = math.exp(-(((d - age * 230.0) / 18.0) ** 2)) * (1 - age / 1.3)
                        if ring > 0.02:
                            fx += dx / d * ring * 1500.0
                            fy += dy / d * ring * 1500.0
                            tg = max(tg, ring)
                fx -= ox * 60.0
                fy -= oy * 60.0
                vx, vy = (vx + fx * dt) * damp, (vy + fy * dt) * damp
                ox, oy = ox + vx * dt, oy + vy * dt
                g += (tg - g) * (min(1.0, dt * 14.0) if tg > g else min(1.0, dt * 3.5))
                if abs(ox) + abs(oy) + abs(vx) + abs(vy) < 0.05 and g < 0.01:
                    ox = oy = vx = vy = g = 0.0
                else:
                    moving = True
                p[2], p[3], p[4], p[5], p[7] = ox, oy, vx, vy, g

                x, y = hx + ox + px * depth, hy + oy + py * depth
                k = ease_out(_clamp01(asm * 1.4 - (fa / 6.28) * 0.4))
                if k < 1.0:  # sucked in from outside while the panel opens
                    ix, iy = W / 2 + math.cos(fa) * W * 0.9, H / 2 + math.sin(fa) * H * 1.4
                    x, y = ix + (x - ix) * k, iy + (y - iy) * k
                s_ = (1.2 + 1.8 * depth) * (0.4 + 0.6 * k) * (1 + 0.55 * g)
                self.coords(item, x - s_, y - s_, x + s_, y + s_)
                a0 = a * (0.2 + 0.42 * depth)
                alpha = a0 + (1 - a0) * min(1.0, g * 1.1)
                col = _hex(_mixc(bgc, _mixc(base, c_white, 0.7 * g), alpha))
                if col != last:
                    self.itemconfigure(item, fill=col)
                    p[10] = col
            self.moving = moving

        # middle layer: the hidden word, revealed under the cursor and lit by shockwaves
        cs, rad = self.cs, float(self.RAD)
        c_deep = _rgb(DEEP)
        for c in self.cells:
            tgt = 0.0
            if sm is not None:
                t = 1 - math.hypot(c[1] - sm[0], c[2] - sm[1]) / rad
                if t > 0:
                    tgt = t * t * (3 - 2 * t)
            for rx, ry, rt in self.ripples:
                age = now - rt
                d = math.hypot(c[1] - rx, c[2] - ry)
                w = math.exp(-(((d - age * 230.0) / 16.0) ** 2)) * (1 - age / 1.3)
                if w > tgt:
                    tgt = min(1.0, w)
            r = c[3]
            nr = r + (tgt - r) * (0.35 if tgt > r else 0.08)
            if abs(nr - tgt) < 0.004:
                nr = tgt
            if nr == r and (nr < 0.02) == (not c[4]):
                continue
            c[3] = nr
            if nr < 0.02:
                self.itemconfigure(c[0], state="hidden")
                c[4] = False
                continue
            h = (cs / 2 - 0.5) * (0.45 + 0.55 * nr)
            bright = _mixc(c_cyan, c_white, 0.12 + 0.3 * c[5])
            self.coords(c[0], c[1] - h, c[2] - h, c[1] + h, c[2] + h)
            self.itemconfigure(c[0], state="normal", fill=_hex(_mixc(bgc, _mixc(c_deep, bright, nr), a)))
            c[4] = True


class FeatureDeck:
    """The hero section: fades in and rises into place."""

    def __init__(self, parent):
        self.parent = parent
        self.hero = HeroField(parent)
        self.off = 18
        self.hero.pack(fill="x", padx=14, pady=(18, 6))
        self.t0, self.running = None, False

    def start(self, delay=0.0):
        self.t0 = time.time() + delay
        self.hero.restart(self.t0)
        if not self.running:
            self.running = True
            self.tick()

    def replay(self):
        self.start(0.05)

    def tick(self):
        try:
            if not self.parent.winfo_exists():
                self.running = False
                return
            now = time.time()
            off = int(round(18 * (1 - ease_out(_clamp01((now - self.t0) / 0.7)))))
            if off != self.off:
                self.off = off
                self.hero.pack_configure(pady=(off, 6))
            if self.hero.winfo_viewable():
                self.hero.tick(now)
            self.parent.after(33, self.tick)
        except tk.TclError:
            self.running = False


def human_join(items):
    items = [str(i) for i in items]
    if len(items) <= 1:
        return "".join(items)
    return ", ".join(items[:-1]) + " and " + items[-1]


def summarize(results):
    n = len(results)
    if n == 0:
        return "I didn't find any tasks in that. Could you try again?", []
    done = [r for r in results if r["status"] == "done"]
    review = [r for r in done if r.get("needs_approval")]
    stuck = [r for r in results if r["status"] in ("failed", "needs_input")]
    skipped = [r for r in results if r["status"] == "skipped"]

    s = []
    if len(done) == n:
        s.append(f"All done. I completed {n} task{'s' if n != 1 else ''}.")
    else:
        s.append(f"I completed {len(done)} of {n} task{'s' if n != 1 else ''}.")
    if review:
        kinds = {"code": ("the code", "programs"), "email": ("the email draft", "email drafts"),
                 "message": ("the message", "messages"), "other": ("the note", "notes"),
                 "file": ("the file", "files"), "chat_summary": ("the chat summary", "chat summaries")}
        parts = []
        for t, (one, many) in kinds.items():
            k = sum(1 for r in review if r["type"] == t)
            if k:
                parts.append(one if k == 1 else f"the {k} {many}")
        s.append(f"Please check {human_join(parts)} before you use "
                 f"{'it' if len(review) == 1 else 'them'}.")
    if stuck:
        s.append(f"I need your help with {human_join([r['title'] for r in stuck])}.")
    if skipped:
        s.append(f"I skipped {human_join([r['title'] for r in skipped])}, which I can't do yet.")
    for r in done:
        if r["type"] == "reminder" and r.get("note"):
            s.append(r["note"] + ".")
    if any(r["type"] == "email" for r in results):
        s.append("I didn't send any email.")
    if any(r["type"] == "message" for r in results):
        s.append("I didn't send any message.")

    rows = []
    for r in results:
        if r["status"] == "done" and r.get("needs_approval"):
            rows.append((WARN, r["title"], "Done. Please check it"))
        elif r["status"] == "done":
            rows.append((GOOD, r["title"], "Done"))
        elif r["status"] == "failed":
            rows.append((BAD, r["title"], "I couldn't finish this"))
        elif r["status"] == "needs_input":
            rows.append((WARN, r["title"], "Needs your help"))
        else:
            rows.append((MUTED, r["title"], "Skipped"))
    return " ".join(s), rows


# ---------- Ollama / Gemma ----------
def ensure_ollama():
    global _OLLAMA_PROC
    import ollama
    try:
        ollama.list()
        return
    except Exception as e:
        log_ui(f"ollama not reachable ({e}); trying 'ollama serve'")
    try:
        _OLLAMA_PROC = subprocess.Popen(["ollama", "serve"], stdout=subprocess.DEVNULL,
                                        stderr=subprocess.DEVNULL, creationflags=NOWIN)
    except Exception as e:
        log_ui(f"could not start ollama: {e}")
        raise UserError("Ollama isn't running and I couldn't start it. Please open the Ollama app.")
    for _ in range(24):
        time.sleep(0.5)
        try:
            ollama.list()
            log_ui("ollama started by Jarvis")
            return
        except Exception:
            pass
    raise UserError("I can't reach Ollama. Please open the Ollama app and try again.")


def pick_model():
    forced = os.environ.get("JARVIS_MODEL", "").strip()
    if forced:
        return forced
    ensure_ollama()
    try:
        import ollama
        names = [m.model for m in ollama.list().models]
    except Exception as e:
        log_ui(f"ollama.list failed: {e}")
        raise UserError("I can't reach Ollama. Please start Ollama and try again.")
    for want in PREFERRED_MODELS:
        if want in names:
            return want
    raise UserError(f"No Gemma model is installed. Run: ollama pull {PREFERRED_MODELS[0]}")


def pretty_model(name):
    m = re.match(r"^([a-z]+)(\d+(?:\.\d+)?):(\d+b)", name.lower())
    return f"{m.group(1).title()} {m.group(2)} {m.group(3).upper()}" if m else name


# ---------- conversation: understand what the user wants BEFORE doing anything ----------
_VOSK_MODEL = None
_WHISPER = None
WAKE_PREFIX = re.compile(r"^\s*(?:(?:hey|hi|hello|ok|okay)[,\s]+)?(?:jarvis|travis|service|javis|harvest)\b[,.!?\s]*", re.I)
WAKE_SUFFIX = re.compile(r"[,\s]*\b(?:jarvis|travis|javis)\b[.!?\s]*$", re.I)
THANKS_RE = re.compile(r"(?:thanks?|thank you|thank u|thx|cheers|much appreciated|appreciate it)"
                       r"(?: so much| a lot| very much| again| man| mate)?"
                       r"(?: for (?:that|this|everything|your help|helping|the help))?")
FILLER_RE = re.compile(r"^(?:okay|ok|alright|all right|so|um|uh|well|yeah|yes|yep|no|nope|nah|oh|hey|hi)\b[,.\s]*", re.I)
DISMISS_RE = re.compile(r"(?:let (?:it|this|that) be|leave (?:it|this|that)(?: be| alone)?|forget (?:it|about it)|"
                        r"never ?mind|no worries|that'?s (?:all|it|enough|fine)|i'?m (?:good|fine)|all good|"
                        r"it'?s (?:fine|okay|ok|alright)|(?:no|nope|nah)(?: thanks| thank you)?)")
PRAISE_RE = re.compile(r"(?:great|good|nice|awesome|amazing|perfect|well done)(?: job| work)?(?: today)?")
GREET_RE = re.compile(r"(?:hi|hello|hey|yo|howdy|hey there|hi there|hello there|good (?:morning|afternoon|evening))(?: jarvis)?")
STOP_RE = re.compile(r"\b(?:stop|cancel|abort|never ?mind|hold on|hold up|forget (?:it|that)|that'?s enough|stand down|quit|"
                     r"let (?:it|this|that) be|leave (?:it|that|this))\b", re.I)
TASK_RE = re.compile(r"^(?:(?:hey|hi|okay|ok|so|um|uh|well)[,\s]+)*(?:please |can you |could you |would you |i need you to |i want you to |go ahead and |jarvis )*"
                     r"(?:write|draft|send|email|create|make|build|plan|schedule|summari[sz]e|find|search|open|run|"
                     r"generate|prepare|remind|set up|compile|organi[sz]e|fix|code|translate|book|add|check|message|text|ping)\b", re.I)


def strip_wake(text):
    t = text.strip()
    for _ in range(2):
        t = WAKE_PREFIX.sub("", t, count=1)
        t = WAKE_SUFFIX.sub("", t, count=1)
    return t.strip()


def looks_like_task(text):
    return bool(TASK_RE.match(text.strip()))


def _norm_utterance(text, strip_fillers=True):
    """Lowercase, drop wake words and leading fillers ('okay, all right, ...'), so short phrases match."""
    c = re.sub(r"\b(?:jarvis|travis|javis)\b", " ", text.lower())
    c = re.sub(r"[^a-z' ]+", " ", c)
    c = re.sub(r"\s+", " ", c).strip()
    for _ in range(4 if strip_fillers else 0):
        n = FILLER_RE.sub("", c, count=1).strip()
        if not n or n == c:
            break
        c = n
    return c


def quick_reply(clean, busy):
    """Instant answers for the obvious cases, so no model call is needed."""
    raw = clean.lower().strip(" .!?,")
    c = _norm_utterance(clean)
    c0 = _norm_utterance(clean, False)
    if not raw or raw in ("jarvis", "travis"):
        return {"intent": "chat", "reply": "Yes? I'm listening."}
    if busy and STOP_RE.search(raw) and len(raw.split()) <= 8:
        return {"intent": "stop", "reply": "Okay, stopped. What would you like to do instead?", "task": ""}
    if not busy and re.fullmatch(r"(?:no|nope|nah) (?:thanks|thank you)", c0):
        return {"intent": "chat", "reply": "Alright."}
    if THANKS_RE.fullmatch(c):
        return {"intent": "chat", "reply": random.choice(["My pleasure.", "Anytime.", "Happy to help."])}
    if PRAISE_RE.fullmatch(c):
        return {"intent": "chat", "reply": random.choice(["Thank you, glad that worked out.", "Much appreciated."])}
    if GREET_RE.fullmatch(c) or GREET_RE.fullmatch(raw):
        h = datetime.now().hour
        part = "Good morning" if h < 12 else "Good afternoon" if h < 18 else "Good evening"
        return {"intent": "chat", "reply": f"{part}! What can I do for you?"}
    if not busy and re.fullmatch(r"(?:stop|cancel|quit)", c):
        return {"intent": "chat", "reply": "Nothing is running right now."}
    if not busy and DISMISS_RE.fullmatch(c):
        return {"intent": "chat", "reply": random.choice(["No problem.", "Alright, leaving it.", "Okay."])}
    return None


def get_whisper():
    """Loads Whisper once (small.en by default) and reuses it. Falls back to base.en if the bigger one fails."""
    global _WHISPER
    if _WHISPER is None:
        from faster_whisper import WhisperModel
        last = None
        for name in dict.fromkeys((WHISPER_MODEL, "base.en")):
            try:
                _WHISPER = WhisperModel(name, device="cpu", compute_type="int8")
                log_ui(f"whisper loaded: {name}")
                break
            except Exception as e:
                last = e
                log_ui(f"whisper {name} failed: {e}")
        if _WHISPER is None:
            raise last or RuntimeError("whisper unavailable")
    return _WHISPER


def whisper_text(path):
    segs, _info = get_whisper().transcribe(
        str(path), language="en", beam_size=3, vad_filter=True,
        vad_parameters={"min_silence_duration_ms": 500},
        condition_on_previous_text=False,
        initial_prompt="A short spoken message to Jarvis, a personal assistant.")
    return " ".join(sg.text.strip() for sg in segs).strip()


def vosk_text(path):
    with wave.open(str(path), "rb") as wf:
        rate, ch, sw = wf.getframerate(), wf.getnchannels(), wf.getsampwidth()
        raw = wf.readframes(wf.getnframes())
    if sw == 2 and ch == 2 and np is not None:
        raw = np.frombuffer(raw, dtype=np.int16).reshape(-1, 2).mean(axis=1).astype(np.int16).tobytes()
    elif not (sw == 2 and ch == 1):
        raise RuntimeError("unsupported wav format")
    global _VOSK_MODEL
    if _VOSK_MODEL is None:
        vosk.SetLogLevel(-1)
        _VOSK_MODEL = vosk.Model(str(VOSK_MODEL_PATH))
    rec = vosk.KaldiRecognizer(_VOSK_MODEL, rate)
    for i in range(0, len(raw), 8000):
        rec.AcceptWaveform(raw[i:i + 8000])
    return json.loads(rec.FinalResult()).get("text", "").strip()


def quick_transcribe(path):
    """Local speech-to-text for short voice commands. Whisper first (far more accurate),
    the small Vosk model only as a fallback. Returns text, or None if no engine works."""
    try:
        return whisper_text(path)
    except Exception as e:
        log_ui(f"whisper transcription unavailable, trying vosk: {e}")
    if VOSK_AVAILABLE and VOSK_MODEL_PATH.exists():
        try:
            return vosk_text(path)
        except Exception as e:
            log_ui(f"vosk transcription failed: {e}")
    return None


def warm_up_models():
    """Load Whisper and the Gemma model in the background so the first real request is not slow."""
    try:
        get_whisper()
    except Exception as e:
        log_ui(f"whisper warm-up skipped: {e}")
    try:
        import ollama
        ollama.chat(model=pick_model(), messages=[{"role": "user", "content": "hi"}],
                    options={"num_predict": 1, "num_ctx": 2048}, keep_alive="30m")
        log_ui("gemma warmed up")
    except Exception as e:
        log_ui(f"gemma warm-up skipped: {e}")


RECAP_RE = re.compile(r"\b(?:summar\w*|recap|list|show|what|tell me)\b.*\b(?:done|did|completed|worked on)\b", re.I)
REM_Q_RE = re.compile(r"\b(?:what|which|any|show|list|do i have|have i got)\b.*\b(?:reminders?|scheduled|schedule|coming up|"
                      r"appointments?|calls?|meetings?|on today|on tomorrow)\b", re.I)


def history_recap(runs, limit=8):
    """An exact list of what Jarvis really did, built from history.json. No model, so nothing is invented."""
    rows = []
    for e in sorted(runs, key=lambda x: x.get("timestamp", ""), reverse=True):
        for r in e.get("results", []):
            rows.append((e.get("timestamp", "")[:16].replace("T", " "), r))
    if not rows:
        return "I haven't finished any tasks yet."
    word = {"done": "done", "failed": "failed", "needs_input": "needs you", "skipped": "skipped"}
    lines = [f"So far I've handled {len(rows)} task{'s' if len(rows) != 1 else ''}. The latest:"]
    for ts, r in rows[:limit]:
        lines.append(f"{ts}  {r.get('type', 'task')}: {str(r.get('title', ''))[:48]} ({word.get(r.get('status'), r.get('status'))})")
    if len(rows) > limit:
        lines.append(f"...and {len(rows) - limit} older.")
    if any(r.get("type") in ("email", "message") for _, r in rows):
        lines.append("Emails and messages are drafts only. I never send anything.")
    return "\n".join(lines)


def reminders_text():
    up = rem.upcoming(6)
    if not up:
        return ""
    return "\n".join(f"- {rem.fmt_when(dt)}: {it.get('text', '')}" for dt, it in up)


def direct_answer(clean):
    """Questions Jarvis can answer exactly from its own records, without asking the model."""
    if RECAP_RE.search(clean) and re.search(r"\b(?:tasks?|things|work|stuff|everything|so far|till date|to date)\b", clean, re.I):
        return {"intent": "chat", "reply": history_recap(load_history())}
    if looks_like_task(clean):
        return None
    if REM_Q_RE.search(clean):
        t = reminders_text()
        return {"intent": "chat", "reply": ("Here's what's coming up:\n" + t) if t else "You have no reminders set."}
    return None


def convo_system(running_task, runs):
    now = datetime.now().strftime("%A %d %B %Y, %I:%M %p")
    lines = []
    for e in sorted(runs, key=lambda x: x.get("timestamp", ""), reverse=True)[:6]:
        titles = ", ".join(str(r.get("title", ""))[:40] for r in e.get("results", [])[:4])
        lines.append(f'- {e.get("timestamp", "?")}: asked "{str(e.get("prompt", ""))[:140]}" -> '
                     f'{str(e.get("summary", ""))[:220]} (tasks: {titles or "none"})')
    history = "\n".join(lines) or "- nothing yet"
    running = f'YES, currently working on: "{running_task[:200]}"' if running_task else "no"
    return (
        "You are Jarvis, a warm, quick-witted personal assistant living on the user's laptop. "
        "Talk like a person: short, natural, no lists, no markdown, no emojis (replies may be spoken aloud).\n"
        "The user's words come from speech recognition and can contain mistakes. If a message is unclear or makes no "
        'sense, do NOT guess and do NOT start work: use "chat" and ask a short question like "Sorry, did you say ...?".\n'
        'Reply with JSON only: {"intent": "chat" | "task" | "stop", "reply": "...", "task": "..."}\n'
        "Rules:\n"
        '- "task": ONLY when the user clearly asks you to DO a piece of work (write, draft, plan, summarize files, '
        'create code, send, schedule...). Put the full request in "task". "reply" is a tiny acknowledgement (max 6 words).\n'
        '- "stop": only if a task is running and the user wants it stopped or replaced. "reply" is a short acknowledgement. '
        'If they also gave a new job, put it in "task"; otherwise leave "task" empty.\n'
        '- "chat": everything else: greetings, thanks, questions, small talk, "never mind", "leave it", asking what was '
        "done earlier or how the current job is going. Answer in 1 to 2 short sentences using the context below. "
        "Never start work for these.\n"
        'If you are not sure the user wants work done, choose "chat" and ask. Never claim to have done anything that is '
        "not in the context.\n"
        '- Writing a text/WhatsApp message, drafting an email, setting a reminder, scheduling a call, creating a file, '
        'writing code and planning are all "task". Jarvis only DRAFTS emails and messages: it never sends anything and '
        "never phones anyone. Reminders are saved and Jarvis itself shows them at the right time.\n"
        '- If a request is big or vague (for example "build something that copies you"), do NOT stall with filler like '
        '"let me think". Use "chat" and ask ONE specific question about what exactly they want.\n'
        '- Never say you emailed, sent or called anyone. Say "drafted" for emails and messages.\n'
        '- Questions about reminders or the schedule: answer only from "Upcoming reminders" below.\n\n'
        "Examples:\n"
        'User: thank you so much -> {"intent":"chat","reply":"Anytime.","task":""}\n'
        'User: no, all right, let this be -> {"intent":"chat","reply":"No problem, I\'ll leave it.","task":""}\n'
        'User: how are you today -> {"intent":"chat","reply":"Doing well, thanks. What can I do for you?","task":""}\n'
        'User: draft an email to my professor asking for an extension -> '
        '{"intent":"task","reply":"On it.","task":"Draft an email to my professor asking for an extension"}\n'
        'User: remind me to submit the lab report tomorrow at 5pm -> '
        '{"intent":"task","reply":"Setting that reminder.","task":"Remind me to submit the lab report tomorrow at 5pm"}\n'
        'User: write a message to Ali saying I will be late -> '
        '{"intent":"task","reply":"Writing it now.","task":"Write a message to Ali saying I will be late"}\n'
        'User: (a job is running) stop that and plan my day instead -> '
        '{"intent":"stop","reply":"Okay, switching.","task":"Plan my day"}\n\n'
        f"Now: {now}\nTask running right now: {running}\nUpcoming reminders:\n{reminders_text() or '- none'}\n"
        f"Recent runs (newest first):\n{history}"
    )


def llm_route(text, system, log):
    import ollama
    model = pick_model()
    msgs = [{"role": "system", "content": system}] + list(log[-6:]) + [{"role": "user", "content": text}]
    r = ollama.chat(model=model, messages=msgs, format="json", keep_alive="30m",
                    options={"temperature": 0.4, "num_predict": 160, "num_ctx": 3072})
    content = r["message"]["content"]
    try:
        data = json.loads(content)
    except ValueError:
        m = re.search(r"\{.*\}", content, re.S)
        try:
            data = json.loads(m.group(0)) if m else {}
        except ValueError:
            data = {}
        if not data:
            data = {"intent": "chat", "reply": content.strip()}
    if data.get("intent") not in ("chat", "task", "stop"):
        data["intent"] = "chat"
    return data


# ---------- Live task checklist ----------
class LiveChecklist:
    """Displays real-time stages and tasks with ticking checkmarks, progress, and Cancel button."""
    def __init__(self, parent, on_cancel, mode="text"):
        self.parent = parent
        self.on_cancel = on_cancel
        self.mode = mode
        self.finished = False
        self.rows = {}
        self.active_idx = None

        self.wrap = tk.Frame(parent, bg=BG)
        self.wrap.pack(fill="x", padx=14, pady=5)

        self.outer = tk.Frame(self.wrap, bg=BUBBLE)
        self.outer.pack(anchor="w", fill="x")

        self.accent_bar = tk.Frame(self.outer, bg=ACCENT, width=3)
        self.accent_bar.pack(side="left", fill="y")

        self.box = tk.Frame(self.outer, bg=BUBBLE, padx=14, pady=10)
        self.box.pack(side="left", fill="x", expand=True)

        top = tk.Frame(self.box, bg=BUBBLE)
        top.pack(fill="x", pady=(0, 8))

        self.title_lbl = tk.Label(top, text="⚡ Working on tasks...", font=("Segoe UI", 10, "bold"),
                                  fg=TEXT, bg=BUBBLE)
        self.title_lbl.pack(side="left")

        self.cancel_btn = tk.Button(top, text="✖ Cancel", bg="#3a2024", fg="#ef6461",
                                    activebackground="#4f262c", activeforeground="#ff7d7a",
                                    relief="flat", font=("Segoe UI", 9, "bold"), padx=8, pady=2,
                                    cursor="hand2", command=self.on_cancel)
        self.cancel_btn.pack(side="right")

        self.items_frame = tk.Frame(self.box, bg=BUBBLE)
        self.items_frame.pack(fill="x")

        if mode == "voice":
            self.set_stage("transcribe", "active", "Transcribing voice note with Whisper...")
        else:
            self.set_stage("extract", "active", "Analyzing tasks with Gemma...")

    def set_stage(self, key, status, text):
        if self.finished:
            return
        if key not in self.rows:
            rf = tk.Frame(self.items_frame, bg=BUBBLE)
            rf.pack(fill="x", pady=2)
            il = tk.Label(rf, text="●" if status == "active" else "✔",
                          fg=ACCENT if status == "active" else GOOD,
                          bg=BUBBLE, font=("Segoe UI", 9), width=2, anchor="w")
            il.pack(side="left")
            tl = tk.Label(rf, text=text, fg=TEXT if status == "active" else MUTED,
                          bg=BUBBLE, font=("Segoe UI", 9), wraplength=280, justify="left")
            tl.pack(side="left", fill="x", expand=True)
            self.rows[key] = {"frame": rf, "icon": il, "text": tl}
        else:
            r = self.rows[key]
            if status == "active":
                r["icon"].config(text="●", fg=ACCENT)
                r["text"].config(text=text, fg=TEXT)
            elif status == "done":
                r["icon"].config(text="✔", fg=GOOD)
                r["text"].config(text=text, fg=MUTED)

    def plan_tasks(self, tasks):
        if self.finished:
            return
        for i, t in enumerate(tasks, 1):
            key = f"task_{i}"
            if key not in self.rows:
                rf = tk.Frame(self.items_frame, bg=BUBBLE)
                rf.pack(fill="x", pady=2)
                il = tk.Label(rf, text="○", fg=MUTED, bg=BUBBLE, font=("Segoe UI", 9), width=2, anchor="w")
                il.pack(side="left")
                title = f"Task {i}: ({t.get('type', 'task')}) {t.get('title', 'Task')}"
                tl = tk.Label(rf, text=title, fg=MUTED, bg=BUBBLE, font=("Segoe UI", 9),
                              wraplength=280, justify="left")
                tl.pack(side="left", fill="x", expand=True)
                self.rows[key] = {"frame": rf, "icon": il, "text": tl, "base_title": title}

    def task_start(self, idx, total, ttype, title):
        if self.finished:
            return
        self.active_idx = idx
        key = f"task_{idx}"
        full_title = f"Task {idx}/{total}: ({ttype}) {title}"
        if key in self.rows:
            r = self.rows[key]
            r["icon"].config(text="●", fg=ACCENT)
            r["text"].config(text=full_title + " (running...)", fg=TEXT)
        else:
            rf = tk.Frame(self.items_frame, bg=BUBBLE)
            rf.pack(fill="x", pady=2)
            il = tk.Label(rf, text="●", fg=ACCENT, bg=BUBBLE, font=("Segoe UI", 9), width=2, anchor="w")
            il.pack(side="left")
            tl = tk.Label(rf, text=full_title + " (running...)", fg=TEXT, bg=BUBBLE, font=("Segoe UI", 9),
                          wraplength=280, justify="left")
            tl.pack(side="left", fill="x", expand=True)
            self.rows[key] = {"frame": rf, "icon": il, "text": tl, "base_title": full_title}

    def task_done(self, dur=""):
        if self.finished or not self.active_idx:
            return
        key = f"task_{self.active_idx}"
        if key in self.rows:
            r = self.rows[key]
            base = r.get("base_title", f"Task {self.active_idx}")
            extra = f" (done in {dur})" if dur else " (done)"
            r["icon"].config(text="✔", fg=GOOD)
            r["text"].config(text=base + extra, fg=MUTED)

    def mark_completed(self):
        self.finished = True
        self.title_lbl.config(text="✔ Tasks completed", fg=GOOD)
        self.accent_bar.config(bg=GOOD)
        self.cancel_btn.pack_forget()

    def mark_cancelled(self):
        self.finished = True
        self.title_lbl.config(text="✖ Run cancelled", fg=BAD)
        self.accent_bar.config(bg=BAD)
        self.cancel_btn.pack_forget()


class Jarvis:
    def __init__(self):
        self.root = tk.Tk()
        self.root.title("Jarvis")
        self.root.overrideredirect(True)
        self.root.attributes("-topmost", True)
        self.root.configure(bg=KEYCOL)
        try:
            self.root.attributes("-transparentcolor", KEYCOL)
        except tk.TclError:
            pass
        self.sw, self.sh = self.root.winfo_screenwidth(), self.root.winfo_screenheight()
        self.orb = tk.Canvas(self.root, bg=KEYCOL, highlightthickness=0)
        self.orb.pack(fill="both", expand=True)

        self.busy = False
        self.thinking = False
        self.notice = None
        self.pending_task = None
        self.chat_log = []
        self.cancel_requested = False
        self.current_proc = None
        self.current_mode = "text"
        self.current_payload = ""
        self.live_checklist = None
        self.current_tab = "chat"
        self.recording = False
        self.panel = None
        self.panel_side = "left"
        self.strip = None
        self.strip_active = True
        self.speech_proc = None
        self.last_narration = ""
        self.q = queue.Queue()
        self.mic = None
        self.state_lbl = None
        self.model_lbl = None
        self.model_text, self.model_color = "Checking Ollama...", MUTED

        # voice activity detection (auto-silence stop)
        self.has_spoken = False
        self.last_speech_time = 0.0

        # wake word & global hotkey
        self.wake_word_enabled = True
        self.wake_word_running = True
        self.hotkey_running = True
        self.hotkey_thread_id = None

        # voice output bookkeeping
        self.play_pcm = None
        self.play_t0 = 0.0
        self.audio_until = 0.0
        self.speak_pending = False
        self.speak_gen = 0
        self.meter24 = Meter(24000) if np is not None else None

        # logo animation state
        self.phase = "grow"
        self.t_phase = time.time()
        self.last_t = time.time()
        self.rot = [0.0, 0.0, 0.0]
        self.spin = 0.0
        self.spin_force = True
        self.disp = [0.0] * NBARS
        self.R = 24.0
        self.text_on = False
        self.text_h = TEXT_H
        self.center = (self.sw / 2, self.sh / 2 - 40)
        self.dock_center = (self.sw - DOCK_MARGIN_X - DOCK_R - PAD / 2,
                            self.sh - DOCK_MARGIN_Y - DOCK_R - PAD / 2)
        self.dirty = True
        self.greeted = False
        self.place_logo(self.center, self.R, self.text_h)
        install_button_hover(self.root)
        self.vortex = None
        try:
            self.vortex = PixelVortex(self.root, self.sw, self.sh, self.center)
            self.root.lift()
            self.root.attributes("-topmost", True)
        except Exception as e:
            log_ui(f"pixel vortex skipped: {e}")
            self.vortex = None

        self.orb.bind("<ButtonPress-1>", self.orb_press)
        self.orb.bind("<B1-Motion>", self.orb_drag)
        self.orb.bind("<ButtonRelease-1>", self.orb_release)
        self.orb.bind("<Button-3>", self.orb_menu)

        threading.Thread(target=self.model_check_worker, daemon=True).start()
        threading.Thread(target=warm_up_models, daemon=True).start()
        self.start_wake_word_listener()
        self.start_hotkey()

        self.root.after(100, self.poll)
        self.root.after(33, self.tick)
        self.root.after(4000, self.check_reminders)

    # ---------- the logo window ----------
    def place_logo(self, center, R, text_h):
        w = int(2 * R + PAD)
        h = int(2 * R + PAD + text_h)
        x = int(center[0] - w / 2)
        y = int(center[1] - (R + PAD / 2))
        self.root.geometry(f"{w}x{h}+{x}+{y}")

    def update_intro(self, now):
        if self.phase == "done":
            return False
        t = now - self.t_phase
        if self.phase == "grow":
            p = min(1.0, t / GROW_S)
            self.R = lerp(24, INTRO_R, ease_out(p))
            self.text_on, self.text_h, self.spin_force = False, TEXT_H, True
            self.place_logo(self.center, self.R, self.text_h)
            if p >= 1.0:
                self.phase, self.t_phase = "greet", now
                if GREET_ALOUD and not self.greeted:
                    self.greeted = True
                    self.speak(GREETING)
        elif self.phase == "greet":
            self.R, self.text_on, self.text_h = INTRO_R, True, TEXT_H
            self.spin_force = t < 0.7
            if t >= 2.4 and (not self.is_speaking() or t > 8.0):
                self.phase, self.t_phase = "dock", now
        elif self.phase == "dock":
            p = min(1.0, t / DOCK_S)
            e = ease_in_out(p)
            self.R = lerp(INTRO_R, DOCK_R, e)
            self.text_on, self.text_h = False, TEXT_H * (1 - e)
            self.spin_force = p < 0.7
            c = (lerp(self.center[0], self.dock_center[0], e), lerp(self.center[1], self.dock_center[1], e))
            self.place_logo(c, self.R, self.text_h)
            if p >= 1.0:
                self.phase, self.spin_force = "done", False
                self.R, self.text_h = DOCK_R, 0
                self.place_logo(self.dock_center, DOCK_R, 0)
                self.open_panel_animated()
        return True

    def bar_targets(self, now):
        bands = None
        if self.recording:
            bands = self.mic.bands if self.mic else [v * 0.5 for v in speech_bands(now)]
        elif self.play_pcm is not None and now < self.audio_until and self.meter24 is not None:
            idx = int((now - self.play_t0) * 24000)
            seg = self.play_pcm[max(0, idx - Meter.N):idx]
            bands = self.meter24.bands(seg) if len(seg) else None
        elif now < self.audio_until or (self.speech_proc is not None and self.speech_proc.poll() is None):
            bands = speech_bands(now)
        elif self.phase == "grow":
            bands = boot_bands(now, min(1.0, (now - self.t_phase) / GROW_S))
        if bands is None:
            return [0.0] * NBARS
        return [bands[BAR_BAND[i]] for i in range(NBARS)]

    def tick(self):
        now = time.time()
        dt = min(0.1, now - self.last_t)
        self.last_t = now
        animating = self.update_intro(now)
        if self.notice:
            self.dirty = True
        if self.vortex is not None:
            if self.phase in ("dock", "done"):
                self.vortex.spawning = False
            if not self.vortex.step(dt):
                self.vortex = None

        want = 1.0 if (self.busy or self.spin_force or self.thinking) else 0.0
        self.spin += (want - self.spin) * min(1.0, dt * 2.5)
        if want == 0.0 and self.spin < 0.004:
            self.spin = 0.0
        for i in range(3):
            self.rot[i] = (self.rot[i] + SPEEDS[i] * self.spin * dt) % 360

        target = self.bar_targets(now)
        for i, tv in enumerate(target):
            d = self.disp[i]
            d += (tv - d) * (0.55 if tv > d else 0.18)
            self.disp[i] = d if d > 0.004 else 0.0

        if animating or self.spin > 0 or any(self.disp) or self.dirty:
            self.dirty = False
            w = 2 * self.R + PAD
            cx, cy = w / 2, self.R + PAD / 2
            self.orb.delete("all")
            draw_hud(self.orb, cx, cy, self.R, self.rot, self.disp, True)
            if self.notice and self.phase == "done":
                self.draw_notice(cx, cy, now)
            if self.text_on:
                f = ("Segoe UI", 17)
                ty = 2 * self.R + PAD + TEXT_H / 2
                self.orb.create_text(cx + 1, ty + 1, text=GREETING, font=f, fill=SHADOW)
                self.orb.create_text(cx, ty, text=GREETING, font=f, fill=WHITE)
        self.draw_strip(now)
        self.root.after(33, self.tick)

    def draw_notice(self, cx, cy, now):
        """Silent 'task done' message on the logo itself."""
        sym, word, col = self.notice
        R = self.R
        pulse = 0.5 + 0.5 * math.sin(now * 3.2)
        r = R * 0.985
        self.orb.create_oval(cx - r, cy - r, cx + r, cy + r, outline=mix(DIM, col, 0.35 + 0.65 * pulse),
                             width=max(2, R * 0.045))
        d = R * 0.43
        self.orb.create_oval(cx - d, cy - d, cx + d, cy + d, fill=SHADOW, outline=col, width=2)
        self.orb.create_text(cx, cy - R * 0.10, text=sym, font=("Segoe UI Symbol", -max(10, int(R * 0.30)), "bold"),
                             fill=col)
        self.orb.create_text(cx, cy + R * 0.20, text=word, font=("Segoe UI", -max(7, int(R * 0.11)), "bold"),
                             fill=WHITE)

    def draw_strip(self, now):
        s = self.strip
        if s is None or not self.panel or not self.panel.winfo_exists() or self.panel.state() != "normal":
            return
        voice = any(self.disp)
        active = voice or self.busy
        if not active and not self.strip_active:
            return
        w, h = s.winfo_width(), s.winfo_height()
        if w < 40:
            return
        self.strip_active = active
        mid, x0 = h / 2, 14
        span = w - 2 * x0
        s.delete("all")
        s.create_line(x0, mid, w - x0, mid, fill=DIM, width=1)
        if voice:
            n = 56
            for k in range(n):
                v = self.disp[int(k / n * NBARS)]
                x = x0 + span * (k + 0.5) / n
                half = 1 + v * (mid - 2)
                col = mix(DEEP, BAD, 0.3 + 0.7 * v) if self.recording else mix(BLUE, CYAN, v)
                s.create_line(x, mid - half, x, mid + half, fill=col, width=3, capstyle="round")
        elif self.busy:
            seg, steps = 0.30, 24
            head = -seg + (1 + 2 * seg) * ((now * 0.55) % 1.0)
            for k in range(steps):
                a = head - seg + seg * k / steps
                b = a + seg / steps
                if b <= 0 or a >= 1:
                    continue
                a, b = max(0.0, a), min(1.0, b)
                s.create_line(x0 + span * a, mid, x0 + span * b, mid,
                              fill=mix(DIM, CYAN, k / steps), width=3)

    # ---------- orb mouse handling ----------
    def orb_press(self, e):
        self.px, self.py, self.moved = e.x_root, e.y_root, False
        self.wx, self.wy = self.root.winfo_x(), self.root.winfo_y()

    def orb_drag(self, e):
        if self.phase != "done":
            return
        dx, dy = e.x_root - self.px, e.y_root - self.py
        if abs(dx) + abs(dy) > 4:
            self.moved = True
            self.root.geometry(f"+{self.wx + dx}+{self.wy + dy}")

    def orb_release(self, e):
        if self.phase == "done" and not self.moved:
            self.on_logo_clicked()

    def on_logo_clicked(self):
        """Clicking the logo detects you want to speak and starts recording immediately."""
        if self.recording:
            self.toggle_record()
        elif self.busy:
            self.show_panel()
        else:
            self.show_panel()
            if winsound is not None:
                try:
                    winsound.MessageBeep(winsound.MB_OK)
                except Exception:
                    pass
            self.toggle_record()

    def orb_menu(self, e):
        m = tk.Menu(self.root, tearoff=0)
        m.add_command(label="Open Jarvis", command=self.show_panel)
        m.add_command(label="Voice Key Settings...", command=self.show_voice_settings)
        wake_txt = f"Wake Word: {'ON' if self.wake_word_enabled else 'OFF'} ('Hey Jarvis')"
        m.add_command(label=wake_txt, command=self.toggle_wake_word)
        m.add_command(label="Global Hotkey: Ctrl+Alt+J", state="disabled")
        m.add_separator()
        m.add_command(label="Quit", command=self.quit)
        m.tk_popup(e.x_root, e.y_root)

    def toggle_wake_word(self):
        self.wake_word_enabled = not self.wake_word_enabled
        status = "enabled" if self.wake_word_enabled else "disabled"
        log_ui(f"Wake word {status}")
        self.set_state(f"Wake word {status}", ACCENT)
        self.root.after(2000, lambda: None if self.busy or self.recording else self.set_state("Ready"))

    def quit(self):
        self.wake_word_running = False
        self.wake_word_enabled = False
        self.stop_hotkey()
        self.stop_voice()
        if self.recording:
            if self.mic:
                self.mic.cancel()
            else:
                mci("close rec", ignore=True)
        if self.current_proc is not None:
            try:
                subprocess.run(["taskkill", "/F", "/T", "/PID", str(self.current_proc.pid)],
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=NOWIN)
            except Exception:
                try:
                    self.current_proc.kill()
                except Exception:
                    pass
        if _OLLAMA_PROC is not None and _OLLAMA_PROC.poll() is None:
            _OLLAMA_PROC.terminate()
        self.root.destroy()

    # ---------- chat window ----------
    def panel_geom(self):
        self.root.update_idletasks()
        w, h = PANEL_W, min(PANEL_H, self.sh - 110)
        ox, oy = self.root.winfo_x(), self.root.winfo_y()
        ow, oh = self.root.winfo_width(), self.root.winfo_height()
        self.panel_side = "left"
        x = ox - w - 12
        if x < 10:
            self.panel_side = "right"
            x = min(ox + ow + 12, self.sw - w - 10)
        y = max(10, min(oy + oh - h, self.sh - h - 60))
        return w, h, x, y

    def toggle_panel(self):
        if self.panel and self.panel.winfo_exists() and self.panel.state() == "normal":
            self.panel.withdraw()
        else:
            self.show_panel()

    def show_panel(self):
        self.notice = None
        if not (self.panel and self.panel.winfo_exists()):
            self.build_panel()
        elif self.panel.state() != "normal":
            w, h, x, y = self.panel_geom()
            self.panel.geometry(f"{w}x{h}+{x}+{y}")
            if getattr(self, "deck", None):
                self.deck.replay()
        self.panel.attributes("-alpha", 1.0)
        self.panel.deiconify()
        self.panel.lift()

    def open_panel_animated(self):
        if not (self.panel and self.panel.winfo_exists()):
            self.build_panel()
        w, h, x, y = self.panel_geom()
        p = self.panel
        p.attributes("-alpha", 0.0)
        off = 40 if self.panel_side == "left" else -40
        p.geometry(f"{w}x{h}+{x + off}+{y}")
        p.deiconify()
        p.lift()
        t0 = time.time()

        def step():
            if not p.winfo_exists():
                return
            k = min(1.0, (time.time() - t0) / 0.35)
            e = ease_out(k)
            p.attributes("-alpha", e)
            p.geometry(f"{w}x{h}+{int(x + off * (1 - e))}+{y}")
            if k < 1.0:
                self.root.after(16, step)

        step()

    def build_panel(self):
        p = tk.Toplevel(self.root)
        p.withdraw()
        self.panel = p
        p.title("Jarvis")
        p.configure(bg=BG)
        p.attributes("-topmost", True)
        w, h, x, y = self.panel_geom()
        p.geometry(f"{w}x{h}+{x}+{y}")
        p.minsize(380, 440)
        p.protocol("WM_DELETE_WINDOW", p.withdraw)
        OrganicShape(p).start()

        head = tk.Frame(p, bg=PANEL)
        head.pack(fill="x")

        # Right side: view toggle button and voice settings button (packed FIRST so never clipped)
        head_right = tk.Frame(head, bg=PANEL)
        head_right.pack(side="right", padx=(4, 14), pady=8)

        self.view_toggle_btn = tk.Button(
            head_right, text="📜 History", font=("Segoe UI", 9, "bold"),
            bg="#232a38", fg=ACCENT, relief="flat", padx=10, pady=4,
            cursor="hand2", activebackground="#2e3748", activeforeground="white",
            command=self.toggle_view
        )
        self.view_toggle_btn.pack(side="right", padx=(4, 0))

        self.key_btn = tk.Button(
            head_right, text="⚙️ Voice", font=("Segoe UI", 8),
            bg="#1c212c", fg=MUTED, relief="flat", padx=6, pady=4,
            cursor="hand2", activebackground="#242b38", activeforeground=TEXT,
            command=self.show_voice_settings
        )
        self.key_btn.pack(side="right", padx=(0, 4))

        # Left side: logo & names
        logo = tk.Canvas(head, width=48, height=48, bg=PANEL, highlightthickness=0)
        logo.pack(side="left", padx=(14, 8), pady=8)
        draw_hud(logo, 24, 24, 21, (0, 0, 0), [0.0] * NBARS, text=False)

        names = tk.Frame(head, bg=PANEL)
        names.pack(side="left", fill="x", expand=True)
        tk.Label(names, text="Jarvis", font=("Segoe UI", 13, "bold"), fg=TEXT, bg=PANEL).pack(anchor="w")
        self.state_lbl = tk.Label(names, text="Ready", font=("Segoe UI", 9), fg=GOOD, bg=PANEL)
        self.state_lbl.pack(anchor="w")
        self.model_lbl = tk.Label(names, text=self.model_text, font=("Segoe UI", 8),
                                  fg=self.model_color, bg=PANEL, wraplength=180, justify="left")
        self.model_lbl.pack(anchor="w")

        self.strip = tk.Canvas(p, height=22, bg=PANEL, highlightthickness=0)
        self.strip.pack(fill="x")
        self.strip_active = True
        tk.Frame(p, height=1, bg="#232a38").pack(fill="x")

        # Views container
        self.views_container = tk.Frame(p, bg=BG)
        self.views_container.pack(fill="both", expand=True)

        # Chat view
        self.chat_view = tk.Frame(self.views_container, bg=BG)
        self.chat_view.pack(fill="both", expand=True)

        self.bar = tk.Canvas(self.chat_view, height=76, bg=BG, highlightthickness=0)
        self.bar.pack(side="bottom", fill="x")
        body = tk.Frame(self.chat_view, bg=BG)
        body.pack(fill="both", expand=True)
        self.canvas = tk.Canvas(body, bg=BG, highlightthickness=0)
        self.canvas.pack(fill="both", expand=True)
        self.chat = tk.Frame(self.canvas, bg=BG)
        self.cwin = self.canvas.create_window((0, 0), window=self.chat, anchor="nw")
        self.canvas.bind("<Configure>", lambda e: self.canvas.itemconfigure(self.cwin, width=e.width))
        self.chat.bind("<Configure>", lambda e: self.canvas.configure(scrollregion=self.canvas.bbox("all")))
        p.bind_all("<MouseWheel>", self.on_mousewheel)

        self.build_input_bar()
        self.build_history_view()
        self.deck = FeatureDeck(self.chat)

        hour = datetime.now().hour
        hello = "Good morning" if hour < 12 else "Good afternoon" if hour < 18 else "Good evening"
        self.bubble("jarvis", f"{hello}! What's on your mind today?\n"
                              "What tasks should I get done? Type them, or say 'Hey Jarvis'.")
        self.render_action_chips()
        self.deck.start(0.35)
        self.panel.after(160, lambda: self.canvas.yview_moveto(0.0))

    def on_mousewheel(self, e):
        delta = int(-e.delta / 120)
        if getattr(self, "current_tab", "chat") == "history" and hasattr(self, "hist_canvas"):
            self.hist_canvas.yview_scroll(delta, "units")
        elif hasattr(self, "canvas"):
            self.canvas.yview_scroll(delta, "units")

    # ---------- view navigation ----------
    def toggle_view(self):
        if self.current_tab == "chat":
            self.show_history_view()
        else:
            self.show_chat_view()

    def show_chat_view(self):
        self.current_tab = "chat"
        if hasattr(self, "history_view"):
            self.history_view.pack_forget()
        if hasattr(self, "chat_view"):
            self.chat_view.pack(fill="both", expand=True)
        if hasattr(self, "view_toggle_btn"):
            self.view_toggle_btn.config(text="📜 History", bg="#232a38", fg=ACCENT)

    def show_history_view(self):
        self.current_tab = "history"
        if hasattr(self, "chat_view"):
            self.chat_view.pack_forget()
        if hasattr(self, "history_view"):
            self.history_view.pack(fill="both", expand=True)
        if hasattr(self, "view_toggle_btn"):
            self.view_toggle_btn.config(text="💬 Back to Chat", bg=USER_BG, fg="white")
        self.render_history_cards()

    # ---------- action chips ----------
    def render_action_chips(self):
        wrap = tk.Frame(self.chat, bg=BG)
        wrap.pack(fill="x", padx=14, pady=(2, 8))
        tk.Label(wrap, text="Quick suggestions:", font=("Segoe UI", 8), fg=MUTED, bg=BG).pack(anchor="w", pady=(0, 4))

        chips = [
            ("📋 Plan my day", "Plan my schedule for today: review project code, draft team email, and summarize morning notes."),
            ("✉️ Draft an email", "Draft a professional email to Professor asking for feedback on my assignment submission."),
            ("🐍 Python script", "Write a Python script that calculates compound interest with interactive input and prints a formatted table."),
            ("📊 Summarize notes", "Summarize the exported meeting notes and list all open action items."),
        ]

        grid = tk.Frame(wrap, bg=BG)
        grid.pack(fill="x")
        grid.columnconfigure(0, weight=1, uniform="chips")
        grid.columnconfigure(1, weight=1, uniform="chips")

        for i, (chip_text, prompt) in enumerate(chips):
            btn = tk.Button(grid, text=chip_text, bg="#232a38", fg=ACCENT,
                            font=("Segoe UI", 9), relief="flat", padx=10, pady=4,
                            cursor="hand2", activebackground="#2e3748", activeforeground="white",
                            command=lambda p=prompt: self.apply_action_chip(p))
            btn.grid(row=i // 2, column=i % 2, sticky="ew",
                     padx=(0, 3) if i % 2 == 0 else (3, 0), pady=3)
            fade_in_button(btn, 0.8 + i * 0.1)

    def apply_action_chip(self, prompt):
        self.clear_placeholder()
        self.entry.delete("1.0", "end")
        self.entry.insert("1.0", prompt)
        self.entry.focus_set()
        self.entry.mark_set("insert", "end")
        if self.panel:
            self.panel.after(60, lambda: self.canvas.yview_moveto(1.0))

    # ---------- history view ----------
    def build_history_view(self):
        self.history_view = tk.Frame(self.views_container, bg=BG)

        hbar = tk.Frame(self.history_view, bg=PANEL, padx=12, pady=8)
        hbar.pack(fill="x")

        tk.Button(hbar, text="← Back to Chat", font=("Segoe UI", 9, "bold"),
                  bg=USER_BG, fg="white", relief="flat", padx=10, pady=3,
                  cursor="hand2", command=self.show_chat_view).pack(side="left")

        tk.Label(hbar, text="Past Runs & Reports", font=("Segoe UI", 10, "bold"),
                 fg=TEXT, bg=PANEL).pack(side="left", padx=12)

        tk.Button(hbar, text="Clear", font=("Segoe UI", 8),
                  bg="#232a38", fg=MUTED, relief="flat", padx=8, pady=3,
                  cursor="hand2", activebackground="#2e3748", activeforeground=TEXT,
                  command=self.clear_history).pack(side="right")

        self.hist_canvas = tk.Canvas(self.history_view, bg=BG, highlightthickness=0)
        self.hist_canvas.pack(fill="both", expand=True)
        self.hist_inner = tk.Frame(self.hist_canvas, bg=BG)
        self.hist_win = self.hist_canvas.create_window((0, 0), window=self.hist_inner, anchor="nw")

        self.hist_canvas.bind("<Configure>", lambda e: self.hist_canvas.itemconfigure(self.hist_win, width=e.width))
        self.hist_inner.bind("<Configure>", lambda e: self.hist_canvas.configure(scrollregion=self.hist_canvas.bbox("all")))

    def clear_history(self):
        try:
            if HISTORY_FILE.exists():
                HISTORY_FILE.unlink()
        except Exception:
            pass
        self.render_history_cards()

    def use_history_prompt(self, prompt):
        self.show_chat_view()
        self.apply_action_chip(prompt)

    def render_history_cards(self):
        for w in self.hist_inner.winfo_children():
            w.destroy()
        entries = load_history()
        if not entries:
            empty = tk.Frame(self.hist_inner, bg=BG)
            empty.pack(fill="x", pady=40)
            tk.Label(empty, text="No past runs saved yet.", font=("Segoe UI", 11, "bold"),
                     fg=TEXT, bg=BG).pack()
            tk.Label(empty, text="Complete tasks or speak a voice note to view history here.",
                     font=("Segoe UI", 9), fg=MUTED, bg=BG).pack(pady=(4, 0))
            return

        for entry in entries:
            card = tk.Frame(self.hist_inner, bg=CARD)
            card.pack(fill="x", padx=12, pady=6)

            has_error = any(r.get("status") in ("failed", "needs_input") for r in entry.get("results", []))
            accent_col = WARN if has_error else GOOD
            tk.Frame(card, bg=accent_col, width=3).pack(side="left", fill="y")

            body = tk.Frame(card, bg=CARD, padx=12, pady=10)
            body.pack(side="left", fill="x", expand=True)

            hdr = tk.Frame(body, bg=CARD)
            hdr.pack(fill="x")
            ts = entry.get("timestamp", "Recent")
            mode = entry.get("mode", "task").title()
            tk.Label(hdr, text=f"{ts}  \u2022  {mode}", font=("Segoe UI", 9),
                     fg=MUTED, bg=CARD).pack(side="left")

            count = len(entry.get("results", []))
            count_text = f"{count} task{'s' if count != 1 else ''}"
            tk.Label(hdr, text=count_text, font=("Segoe UI", 9, "bold"),
                     fg=accent_col, bg=CARD).pack(side="right")

            prompt = entry.get("prompt", "")
            if prompt and prompt != "Voice command" and prompt != "Previous run":
                p_text = f'"{prompt}"' if len(prompt) <= 120 else f'"{prompt[:120]}..."'
                tk.Label(body, text=p_text, font=("Segoe UI", 9, "italic"),
                         fg=TEXT, bg=CARD, wraplength=300, justify="left").pack(anchor="w", pady=(4, 2))

            summary = entry.get("summary", "")
            if summary:
                tk.Label(body, text=summary, font=("Segoe UI", 9), fg=MUTED,
                         bg=CARD, wraplength=300, justify="left").pack(anchor="w", pady=(2, 6))

            btns = tk.Frame(body, bg=CARD)
            btns.pack(anchor="w", pady=(4, 0))

            if entry.get("report_exists", False):
                tk.Button(btns, text="📄 Report", command=self.open_report,
                          bg="#2a3040", fg=ACCENT, relief="flat", padx=8, pady=2,
                          font=("Segoe UI", 8), cursor="hand2").pack(side="left", padx=(0, 6))
            if summary:
                tk.Button(btns, text="🔊 Listen", command=lambda s=summary: self.speak(s),
                          bg="#2a3040", fg=ACCENT, relief="flat", padx=8, pady=2,
                          font=("Segoe UI", 8), cursor="hand2").pack(side="left", padx=(0, 6))

            shown = [r for r in entry.get("results", []) if r.get("type") in ("email", "code") and r.get("file")]
            if shown:
                r0 = shown[0]
                lbl = "Show Draft" if r0["type"] == "email" else "Show Code"
                tk.Button(btns, text=lbl, command=lambda r=r0: self.show_draft(r),
                          bg="#2a3040", fg=ACCENT, relief="flat", padx=8, pady=2,
                          font=("Segoe UI", 8), cursor="hand2").pack(side="left", padx=(0, 6))

            if prompt and prompt != "Voice command" and prompt != "Previous run":
                tk.Button(btns, text="📋 Use Prompt", command=lambda p=prompt: self.use_history_prompt(p),
                          bg="#2a3040", fg=ACCENT, relief="flat", padx=8, pady=2,
                          font=("Segoe UI", 8), cursor="hand2").pack(side="left")

    # ---------- Voice Settings Dialog ----------
    def show_voice_settings(self):
        top = tk.Toplevel(self.root)
        top.title("Voice Settings")
        top.geometry("380x240")
        top.configure(bg=PANEL)
        top.attributes("-topmost", True)
        top.resizable(False, False)

        tk.Label(top, text="ElevenLabs Voice Setup", font=("Segoe UI", 12, "bold"),
                 fg=TEXT, bg=PANEL).pack(anchor="w", padx=16, pady=(14, 4))

        current_key = get_elevenlabs_key()
        has_key = bool(current_key)
        status_text = f"Status: {'Connected' if has_key else 'Not set (using Windows Voice)'}"
        status_col = GOOD if has_key else WARN
        tk.Label(top, text=status_text, font=("Segoe UI", 9), fg=status_col, bg=PANEL).pack(anchor="w", padx=16)

        tk.Label(top, text="API Key:", font=("Segoe UI", 9), fg=TEXT, bg=PANEL).pack(anchor="w", padx=16, pady=(10, 2))
        key_entry = tk.Entry(top, font=("Segoe UI", 10), bg=BUBBLE, fg=TEXT, insertbackground=TEXT, relief="flat")
        key_entry.pack(fill="x", padx=16, ipady=4)
        if has_key:
            key_entry.insert(0, current_key)

        res_lbl = tk.Label(top, text="", font=("Segoe UI", 8), fg=MUTED, bg=PANEL)
        res_lbl.pack(anchor="w", padx=16, pady=(4, 0))

        def test_and_save():
            new_key = key_entry.get().strip()
            if not new_key:
                res_lbl.config(text="Please paste an API key.", fg=BAD)
                return
            res_lbl.config(text="Testing key with ElevenLabs...", fg=ACCENT)
            top.update()

            # Test against ElevenLabs API
            url = "https://api.elevenlabs.io/v1/user"
            req = urllib.request.Request(url, headers={"xi-api-key": new_key})
            try:
                with urllib.request.urlopen(req, timeout=10) as resp:
                    if resp.status == 200:
                        save_elevenlabs_key(new_key)
                        res_lbl.config(text="✔ Key verified and saved! Testing voice...", fg=GOOD)
                        top.update()
                        self.speak("ElevenLabs voice is now active!")
                        top.after(1500, top.destroy)
            except urllib.error.HTTPError as e:
                res_lbl.config(text=f"Failed: HTTP {e.code} (Invalid API key)", fg=BAD)
            except Exception as e:
                res_lbl.config(text=f"Connection error: {e}", fg=BAD)

        btn_row = tk.Frame(top, bg=PANEL)
        btn_row.pack(fill="x", padx=16, pady=16)
        tk.Button(btn_row, text="Save & Test Key", font=("Segoe UI", 9, "bold"),
                  bg=USER_BG, fg="white", relief="flat", padx=12, pady=4,
                  cursor="hand2", command=test_and_save).pack(side="left")
        tk.Button(btn_row, text="Cancel", font=("Segoe UI", 9),
                  bg="#2a3040", fg=TEXT, relief="flat", padx=10, pady=4,
                  cursor="hand2", command=top.destroy).pack(side="right")

    # ---------- input bar ----------
    def build_input_bar(self):
        bar = self.bar
        self.entry = tk.Text(bar, height=2, wrap="word", font=FONT, bg=BUBBLE, fg=TEXT,
                             insertbackground=TEXT, relief="flat", borderwidth=0, highlightthickness=0)
        self.placeholder_on = False
        self.ph_text = "Type your tasks here..."
        self.set_placeholder()
        self.entry.bind("<FocusIn>", self.clear_placeholder)
        self.entry.bind("<FocusOut>", lambda e: self.set_placeholder())
        self.entry.bind("<Return>", self.on_enter)

        self.attach = IconButton(bar, 30, "#2a3040", "plus", self.run_file, BUBBLE)
        self.mic_btn = IconButton(bar, 34, "#2a3040", "mic", self.toggle_record, BUBBLE)
        self.send = IconButton(bar, 34, USER_BG, "send", self.send_text, BUBBLE)
        self.bar_items = (bar.create_window(0, 0, window=self.attach, anchor="w"),
                          bar.create_window(0, 0, window=self.entry, anchor="w"),
                          bar.create_window(0, 0, window=self.mic_btn, anchor="e"),
                          bar.create_window(0, 0, window=self.send, anchor="e"))
        bar.bind("<Configure>", self.layout_bar)

    def layout_bar(self, e):
        bar, w, h = self.bar, e.width, e.height
        bar.delete("pill")
        round_rect(bar, 14, 10, w - 14, h - 10, 22, fill=BUBBLE, outline="#2a3040", tags="pill")
        bar.tag_lower("pill")
        cy = h / 2
        a, t, m, s = self.bar_items
        bar.coords(a, 24, cy)
        bar.coords(t, 62, cy)
        bar.itemconfigure(t, width=max(50, w - 62 - 100))
        bar.coords(m, w - 56, cy)
        bar.coords(s, w - 20, cy)

    def set_placeholder(self):
        if not self.entry.get("1.0", "end").strip():
            self.entry.delete("1.0", "end")
            self.entry.insert("1.0", self.ph_text)
            self.entry.config(fg=MUTED)
            self.placeholder_on = True

    def clear_placeholder(self, _e=None):
        if self.placeholder_on:
            self.entry.delete("1.0", "end")
            self.entry.config(fg=TEXT)
            self.placeholder_on = False

    def on_enter(self, e):
        if e.state & 0x0001:
            return None
        self.send_text()
        return "break"

    # ---------- chat helpers ----------
    def bubble(self, who, text, rows=(), buttons=(), mono=False):
        mine = who == "user"
        bg = USER_BG if mine else BUBBLE
        wrap = tk.Frame(self.chat, bg=BG)
        wrap.pack(fill="x", padx=14, pady=5)
        outer = tk.Frame(wrap, bg=bg)
        if mine:
            outer.pack(anchor="e")
        else:
            outer.pack(fill="x")  # same left and right edge as the hero, chips and input bar
            tk.Frame(outer, bg=ACCENT, width=3).pack(side="left", fill="y")
        box = tk.Frame(outer, bg=bg, padx=14, pady=10)
        if mine:
            box.pack(side="left")
        else:
            box.pack(side="left", fill="x", expand=True)
        lab = tk.Label(box, text=text, bg=bg, fg="white" if mine else TEXT,
                       font=("Consolas", 9) if mono else FONT, wraplength=320,
                       justify="left")
        lab.pack(anchor="w")
        wrap.bind("<Configure>", lambda e, l=lab, m=mine: l.config(
            wraplength=max(140, min(320, e.width - 60)) if m else max(140, e.width - 36)))
        if rows:
            self.task_visual(box, bg, rows)
        bf = None
        for i, (label, cmd) in enumerate(buttons):
            if i % 2 == 0:
                bf = tk.Frame(box, bg=bg)
                bf.pack(anchor="w", pady=(10 if i == 0 else 6, 0))
            tk.Button(bf, text=label, command=cmd, bg="#2a3040", fg=ACCENT, relief="flat",
                      padx=10, pady=3, font=("Segoe UI", 9), cursor="hand2",
                      activebackground="#343b4d", activeforeground=ACCENT).pack(side="left", padx=(0, 8))
        self.panel.after(60, lambda: self.canvas.yview_moveto(1.0))
        return wrap, lab

    def task_visual(self, box, bg, rows):
        total = len(rows)
        done = sum(1 for _c, _t, sub in rows if sub.startswith("Done"))
        W = 320
        top = tk.Frame(box, bg=bg)
        top.pack(fill="x", pady=(10, 5))
        tk.Label(top, text=f"{done} of {total} done", font=("Segoe UI", 9, "bold"),
                 fg=TEXT, bg=bg).pack(side="left")
        meter = tk.Canvas(box, width=W, height=8, bg=bg, highlightthickness=0)
        meter.pack(anchor="w")
        gap = 3 if total <= 24 else 0
        seg = max(1.0, (W - gap * (total - 1)) / total)
        for i, (color, _t, _s) in enumerate(rows):
            x = i * (seg + gap)
            meter.create_rectangle(x, 0, x + seg, 8, fill=color, outline="")
        for color, title, sub in rows:
            card = tk.Frame(box, bg=CARD)
            card.pack(fill="x", pady=(6, 0))
            tk.Frame(card, bg=color, width=3).pack(side="left", fill="y")
            inner = tk.Frame(card, bg=CARD)
            inner.pack(side="left", fill="x", expand=True, padx=10, pady=6)
            tk.Label(inner, text=title, fg=TEXT, bg=CARD, font=("Segoe UI", 10, "bold"),
                     wraplength=270, justify="left").pack(anchor="w")
            tk.Label(inner, text="\u25cf " + sub, fg=color, bg=CARD,
                     font=("Segoe UI", 9)).pack(anchor="w")

    def set_state(self, text, color=GOOD):
        if self.panel and self.panel.winfo_exists() and self.state_lbl is not None:
            self.state_lbl.config(text=text, fg=color)

    # ---------- sending & cancellation ----------
    def send_text(self):
        has_text = (not self.placeholder_on) and bool(self.entry.get("1.0", "end").strip())
        if self.busy and not has_text:
            self.cancel_current_run()
            return
        if self.placeholder_on:
            return
        text = self.entry.get("1.0", "end").strip()
        if not text or self.recording:
            return
        self.entry.delete("1.0", "end")
        shown = text if len(text) <= 500 else text[:500] + "..."
        self.bubble("user", shown)
        self.converse(text, via_voice=False, audio=None)

    def run_file(self):
        if self.busy or self.recording:
            return
        path = filedialog.askopenfilename(
            title="Choose a voice note",
            filetypes=[("Audio", "*.ogg *.wav *.mp3 *.m4a *.opus"), ("All files", "*.*")])
        if path:
            self.bubble("user", f"Audio file: {Path(path).name}")
            self.start("voice", path)

    def toggle_record(self):
        if not self.recording:
            self.stop_voice()
            self.mic = None
            try:
                rec = MicRecorder()
                rec.start()
                self.mic = rec
            except Exception as e:
                log_ui(f"live recorder unavailable ({e}); using Windows recorder")
                try:
                    rec_start()
                except Exception as e2:
                    log_ui(f"microphone error: {e2}")
                    self.bubble("jarvis", "I couldn't use the microphone. Check sound settings or tap + for audio file.",
                                buttons=[("Show details", self.open_log)])
                    return
            self.recording = True
            self.rec_t0 = time.time()
            self.has_spoken = False
            self.last_speech_time = self.rec_t0
            self.mic_btn.set("stop", "#c53030")
            self.tick_record()
        else:
            self.recording = False
            self.mic_btn.set("mic", "#2a3040")
            took = time.time() - self.rec_t0
            try:
                path = self.mic.stop(VOICE_NOTE) if self.mic else rec_stop()
            except Exception as e:
                log_ui(f"recording error: {e}")
                self.restore_state()
                self.bubble("jarvis", "Something went wrong while saving the recording.",
                            buttons=[("Show details", self.open_log)])
                return
            finally:
                self.mic = None
            if took < 1.2:
                self.restore_state()
                self.bubble("jarvis", "That was too short. Speak your tasks and pause, or type them.")
                return
            self.handle_voice_note(str(path), took)

    def tick_record(self):
        if self.recording:
            now = time.time()
            elapsed = int(now - self.rec_t0)
            self.set_state(f"Listening...  {elapsed // 60}:{elapsed % 60:02d}", BAD)

            # Voice activity detection (auto-stop after silence)
            level = max(self.mic.bands) if (self.mic and hasattr(self.mic, "bands")) else 0.0
            if level > 0.07:
                self.last_speech_time = now
                self.has_spoken = True

            # If user has spoken and paused for 1.4s (min 2.0s total), finish automatically
            if self.has_spoken and (now - self.last_speech_time >= 1.4) and (now - self.rec_t0 >= 2.0):
                log_ui("Auto-silence detected: completing voice note hands-free")
                self.toggle_record()
                return

            if not self.has_spoken and now - self.rec_t0 >= 12.0:
                log_ui("No speech heard; closing the recording")
                self.toggle_record()
                return

            self.root.after(150, self.tick_record)

    # ---------- conversation ----------
    def restore_state(self):
        if self.busy:
            self.set_state("Working on it", ACCENT)
        else:
            self.set_state("Ready")

    def remember(self, user_text, reply):
        self.chat_log += [{"role": "user", "content": user_text}, {"role": "assistant", "content": reply}]
        self.chat_log = self.chat_log[-12:]

    def say(self, text, via_voice):
        self.bubble("jarvis", text)
        if via_voice:
            self.speak(text)

    def handle_voice_note(self, path, took):
        self.thinking = True
        self.dirty = True
        self.set_state("Thinking...", ACCENT)

        def work():
            try:
                heard = quick_transcribe(path)
            except Exception as e:
                log_ui(f"transcription error: {e}")
                heard = None
            self.q.put(("heard", heard, path, took))

        threading.Thread(target=work, daemon=True).start()

    def on_heard(self, heard, path, took):
        self.thinking = False
        self.restore_state()
        if heard is None:  # no speech engine here: behave exactly like before
            if self.busy:
                self.bubble("jarvis", "I can't understand voice notes while I'm working. Press stop, or type it.")
            else:
                self.bubble("user", f"Voice note ({int(took)}s)")
                self.start("voice", path)
            return
        if not heard:
            self.bubble("jarvis", "I didn't catch that.")
            return
        self.bubble("user", heard)
        self.converse(heard, via_voice=True, audio=path)

    def converse(self, text, via_voice, audio):
        """Work out what was meant. Only a clear request for work starts a task."""
        self.notice = None
        clean = strip_wake(text)
        quick = quick_reply(clean or text, self.busy) or direct_answer(clean or text)
        if quick:
            self.apply_route(quick, text, via_voice, audio)
            return
        self.thinking = True
        self.dirty = True
        self.set_state("Thinking...", ACCENT)
        system = convo_system(self.current_payload if self.busy else "", load_history())
        log = list(self.chat_log)

        def work():
            try:
                res = llm_route(clean or text, system, log)
            except UserError as e:
                res = {"intent": "error", "reply": str(e)}
            except Exception as e:
                log_ui(f"conversation error: {e}")
                res = {"intent": "error", "reply": "I couldn't think that through just now."}
            self.q.put(("convo", res, text, via_voice, audio))

        threading.Thread(target=work, daemon=True).start()

    def apply_route(self, res, text, via_voice, audio):
        self.thinking = False
        self.restore_state()
        intent = res.get("intent", "chat")
        reply = (res.get("reply") or "").strip()
        task = (res.get("task") or "").strip()
        clean = strip_wake(text) or text

        if intent == "error":
            if looks_like_task(clean) and not self.busy:  # model unreachable: old behaviour for clear requests
                self.bubble("jarvis", "On it.")
                self.start("voice", audio) if (via_voice and audio) else self.start("text", clean)
                return
            self.say(reply or "Something went wrong.", False)
            return

        if intent == "task":
            if self.busy:
                self.say("I'm still working on something. Say stop if you'd like me to switch to this.", via_voice)
                self.remember(text, "I'm still working on something.")
                return
            ack = reply or "On it."
            self.bubble("jarvis", ack)
            self.remember(text, ack)
            # a spoken request was already transcribed above, so reuse that text: no second Whisper pass,
            # and the busy-status context shows the real request instead of an audio file path
            self.start("text", clean if via_voice else (task or clean))
            if via_voice:
                self.speak(ack)
            return

        if intent == "stop":
            if not self.busy:
                if task:
                    self.bubble("jarvis", reply or "On it.")
                    self.start("text", task)
                else:
                    self.say("Nothing is running right now.", via_voice)
                return
            ack = reply or "Okay, stopping."
            if task:
                self.pending_task = task
                ack += " I'll start the new one right after."
            self.bubble("jarvis", ack)
            self.remember(text, ack)
            self.cancel_current_run()
            if via_voice:
                self.speak(ack)
            return

        if re.search(r"let me think|give me a (?:moment|second)|hmm", reply, re.I) and len(reply) < 90:
            reply = "I want to get this right. Can you tell me exactly what you'd like me to make or do?"
        reply = reply or "I'm here."
        self.say(reply, via_voice)
        self.remember(text, reply)

    def check_reminders(self):
        """Every few seconds: is any saved reminder due? If so, show it and say it."""
        try:
            for item, late in rem.pop_due():
                what = item.get("text", "your reminder")
                msg = (f"Reminder: {what}" if late <= 5 else
                       f"Sorry, I was closed when this was due {late} minutes ago: {what}")
                log_ui(f"reminder fired: {what}")
                self.show_panel()
                self.bubble("jarvis", msg)
                self.chat_log = (self.chat_log + [{"role": "assistant", "content": msg}])[-12:]
                self.notice = ("!", "REMINDER", WARN)
                self.dirty = True
                try:
                    winsound.MessageBeep()
                except Exception:
                    pass
                self.speak(msg)
        except Exception as e:
            log_ui(f"reminder check failed: {e}")
        self.root.after(5000, self.check_reminders)

    def run_pending_task(self):
        if self.pending_task:
            task, self.pending_task = self.pending_task, None
            self.bubble("jarvis", "Starting the new one now.")
            self.root.after(300, lambda: self.start("text", task))

    # ---------- doing the work ----------
    def start(self, mode, payload):
        self.stop_voice()
        self.notice = None
        self.busy = True
        self.cancel_requested = False
        self.current_proc = None
        self.current_mode = mode
        self.current_payload = payload
        self.dirty = True
        self.set_state("Working on it", ACCENT)

        if hasattr(self, "send"):
            self.send.set(icon="stop", fill="#ef6461")

        self.live_checklist = LiveChecklist(self.chat, self.cancel_current_run, mode)
        if self.panel:
            self.panel.after(60, lambda: self.canvas.yview_moveto(1.0))

        threading.Thread(target=self.worker, args=(mode, payload), daemon=True).start()

    def cancel_current_run(self):
        if not self.busy:
            return
        self.cancel_requested = True
        log_ui("User clicked Cancel")
        proc = self.current_proc
        if proc is not None:
            try:
                subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                               creationflags=NOWIN)
            except Exception:
                try:
                    proc.kill()
                except Exception:
                    pass
        self.stop_voice()
        self.q.put(("cancelled", None))

    def sh_run(self, cmd, logf):
        if self.cancel_requested:
            return -1
        env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUTF8="1", PYTHONUNBUFFERED="1")
        try:
            self.current_proc = subprocess.Popen(
                cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                encoding="utf-8", errors="replace", cwd=str(HERE), env=env,
                creationflags=NOWIN
            )
        except Exception as e:
            log_ui(f"failed to start {cmd[0]}: {e}")
            return -1

        proc = self.current_proc
        for line in proc.stdout:
            if self.cancel_requested:
                break
            logf.write(line)
            logf.flush()

            if "Transcribing with Whisper" in line or "Transcribing" in line:
                self.q.put(("cl_stage", "transcribe", "active", "Transcribing voice note with Whisper..."))
            elif "Detected language:" in line:
                lang = line.split("Detected language:")[-1].strip()
                self.q.put(("cl_stage", "transcribe", "done", f"Transcribed voice note ({lang})"))
            elif "Extracting tasks" in line:
                self.q.put(("cl_stage", "extract", "active", "Analyzing tasks with Gemma..."))
            elif "Found " in line and " task" in line:
                m = re.search(r"Found (\d+) task", line)
                if m:
                    n = m.group(1)
                    self.q.put(("cl_stage", "extract", "done", f"Extracted {n} tasks"))
            elif "Starting " in line and " task" in line:
                m = re.search(r"Starting (\d+) task", line)
                if m:
                    n = m.group(1)
                    self.q.put(("cl_stage", "tasks_heading", "active", f"Executing {n} tasks..."))
                self._send_tasks_plan()
            elif "Task " in line and "): " in line:
                m = re.search(r"Task (\d+)/(\d+):\s*\((.*?)\)\s*(.*)", line)
                if m:
                    idx, total, ttype, title = m.groups()
                    self.q.put(("cl_task_start", int(idx), int(total), ttype, title))
            elif "finished in " in line:
                m = re.search(r"finished in\s*(.*)", line)
                if m:
                    dur = m.group(1).strip()
                    self.q.put(("cl_task_done", dur))
            elif "Morning report written to" in line or "All done in" in line:
                self.q.put(("cl_stage", "report", "done", "Morning report compiled"))

        if self.cancel_requested:
            try:
                subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                               creationflags=NOWIN)
            except Exception:
                try:
                    proc.kill()
                except Exception:
                    pass
            self.current_proc = None
            return -1

        rc = proc.wait()
        self.current_proc = None
        return rc

    def _send_tasks_plan(self):
        tasks_file = OUT / "tasks.json"
        if tasks_file.exists():
            try:
                data = json.loads(tasks_file.read_text(encoding="utf-8"))
                tasks = data.get("tasks", [])
                if tasks:
                    self.q.put(("cl_plan_tasks", tasks))
            except Exception:
                pass

    def worker(self, mode, payload):
        py = sys.executable
        t0 = time.time()
        OUT.mkdir(parents=True, exist_ok=True)
        try:
            with UI_LOG.open("w", encoding="utf-8") as logf:
                model = pick_model()
                logf.write(f"model: {model}\nmode: {mode}\n\n")
                if self.cancel_requested:
                    self.q.put(("cancelled", None))
                    return

                if mode == "text":
                    rc = self.sh_run([py, str(HERE / "day1_voice_to_tasks.py"), "--text", payload,
                                      "--model", model], logf)
                    if self.cancel_requested:
                        self.q.put(("cancelled", None))
                        return
                    fresh = (OUT / "tasks.json").exists() and (OUT / "tasks.json").stat().st_mtime >= t0
                    if rc != 0 and not fresh:
                        raise UserError("I couldn't turn that into a task list.")
                    self._send_tasks_plan()
                    if self.sh_run([py, str(HERE / "day3_run_tasks.py"), "--model", model], logf) != 0:
                        if self.cancel_requested:
                            self.q.put(("cancelled", None))
                            return
                        raise UserError("Something went wrong while doing the tasks.")
                else:
                    if self.sh_run([py, str(HERE / "run_overnight.py"), payload, "--model", model], logf) != 0:
                        if self.cancel_requested:
                            self.q.put(("cancelled", None))
                            return
                        raise UserError("Something went wrong while doing the tasks.")

            if self.cancel_requested:
                self.q.put(("cancelled", None))
                return

            results = None
            if RESULTS.exists() and RESULTS.stat().st_mtime >= t0 - 1:
                results = json.loads(RESULTS.read_text(encoding="utf-8"))
            self.q.put(("done", results))
        except UserError as e:
            if not self.cancel_requested:
                self.q.put(("error", str(e)))
        except Exception as e:
            if not self.cancel_requested:
                log_ui(f"worker error: {e}")
                self.q.put(("error", "Something went wrong."))

    def model_check_worker(self):
        try:
            model = pick_model()
            self.q.put(("model", f"{pretty_model(model)}  \u2022  running on this laptop", GOOD))
        except UserError as e:
            self.q.put(("model", str(e), BAD))
        except Exception as e:
            log_ui(f"model check failed: {e}")
            self.q.put(("model", "Couldn't check Ollama", BAD))

    def poll(self):
        try:
            while True:
                item = self.q.get_nowait()
                kind, val = item[0], item[1]
                if kind == "cl_stage":
                    if self.live_checklist:
                        self.live_checklist.set_stage(val, item[2], item[3])
                        if self.panel:
                            self.canvas.yview_moveto(1.0)
                elif kind == "cl_plan_tasks":
                    if self.live_checklist:
                        self.live_checklist.plan_tasks(val)
                        if self.panel:
                            self.canvas.yview_moveto(1.0)
                elif kind == "cl_task_start":
                    if self.live_checklist:
                        self.live_checklist.task_start(val, item[2], item[3], item[4])
                        if self.panel:
                            self.canvas.yview_moveto(1.0)
                elif kind == "cl_task_done":
                    if self.live_checklist:
                        self.live_checklist.task_done(val)
                        if self.panel:
                            self.canvas.yview_moveto(1.0)
                elif kind == "cancelled":
                    self.finish(None, cancelled=True)
                elif kind == "done":
                    self.finish(val)
                elif kind == "error":
                    self.finish(None, error=val)
                elif kind == "wake_word":
                    self.on_wake_word_detected()
                elif kind == "heard":
                    self.on_heard(val, item[2], item[3])
                elif kind == "convo":
                    self.apply_route(val, item[2], item[3], item[4])
                elif kind == "model":
                    self.model_text, self.model_color = val, item[2]
                    if self.model_lbl is not None and self.panel and self.panel.winfo_exists():
                        self.model_lbl.config(text=val, fg=item[2])
        except queue.Empty:
            pass
        self.root.after(100, self.poll)

    def finish(self, results, error=None, cancelled=False):
        self.busy = False
        self.dirty = True
        self.set_state("Ready")
        if hasattr(self, "send"):
            self.send.set(icon="send", fill=USER_BG)

        self.show_panel()

        if cancelled:
            if self.live_checklist:
                self.live_checklist.mark_cancelled()
            self.bubble("jarvis", "Run cancelled. Nothing was executed or sent.")
            self.run_pending_task()
            return

        if error or results is None:
            if self.live_checklist:
                self.live_checklist.finished = True
                self.live_checklist.cancel_btn.pack_forget()
            self.bubble("jarvis", (error or "I finished, but I couldn't read the results.") +
                        " Nothing was sent anywhere.",
                        buttons=[("Show details", self.open_log)])
            self.notice = ("!", "ISSUE", WARN)
            return

        if self.live_checklist:
            self.live_checklist.mark_completed()

        spoken, rows = summarize(results)
        self.last_narration = spoken

        add_history_entry(self.current_mode, self.current_payload, spoken, results)

        words = {"email": "draft", "message": "message", "code": "code", "chat_summary": "summary", "file": "file",
                 "other": "note"}
        shown = [r for r in results if r.get("status") == "done" and r.get("type") != "reminder"
                 and self.task_text(r)[0]]
        buttons = []
        for r in shown:
            word = words.get(r.get("type"), "result")
            same = [x for x in shown if words.get(x.get("type"), "result") == word]
            label = f"Show {word}" if len(same) == 1 else f"{word.title()}: " + str(r.get("title", ""))[:18]
            buttons.append((label, lambda r=r: self.show_draft(r)))
        buttons.append(("Full report", self.open_report))
        self.bubble("jarvis", spoken, rows=rows, buttons=buttons)
        # silent on purpose: the logo shows the result, Jarvis only speaks when spoken to
        needs_you = any(r.get("status") in ("failed", "needs_input") for r in results)
        self.notice = ("!", "NEEDS YOU", WARN) if needs_you else ("\u2714", "DONE", GOOD)

    def task_text(self, r):
        """The finished work for one task: (text, file path or None). Tries the task's own file,
        then any text the task carries, then that task's section of the morning report."""
        f = r.get("file")
        if f:
            try:
                path = Path(f)
                if OUT.resolve() in path.resolve().parents and path.exists():
                    text = path.read_text(encoding="utf-8", errors="replace").strip()
                    if text:
                        return text, path
            except OSError as e:
                log_ui(f"could not read task file: {e}")
        for key in ("output", "result", "text", "content", "draft", "body", "summary"):
            v = r.get(key)
            if isinstance(v, str) and len(v.strip()) > 20:
                return v.strip(), None
        try:
            if REPORT.exists():
                lines = REPORT.read_text(encoding="utf-8", errors="replace").splitlines()
                title = str(r.get("title", "")).strip().lower()
                for i, line in enumerate(lines):
                    if title and title in line.lower() and line.lstrip().startswith(("#", "**")):
                        level = len(line) - len(line.lstrip("#")) if line.startswith("#") else 99
                        body = []
                        for nxt in lines[i + 1:]:
                            if nxt.startswith("#") and (len(nxt) - len(nxt.lstrip("#"))) <= min(level, 3):
                                break
                            body.append(nxt)
                        text = "\n".join(body).strip()
                        if text:
                            return text, REPORT
        except OSError as e:
            log_ui(f"could not read report: {e}")
        return "", None

    def show_draft(self, r):
        text, path = self.task_text(r)
        if not text:
            self.bubble("jarvis", "I couldn't open that one.", buttons=[("Show details", self.open_log)])
            return
        if len(text) > 6000:
            text = text[:6000].rstrip() + "\n\n... (open the file for the rest)"

        def copy():
            self.root.clipboard_clear()
            self.root.clipboard_append(text)
            self.root.update()
            self.set_state("Copied", ACCENT)
            self.root.after(2000, lambda: None if self.busy or self.recording else self.set_state("Ready"))

        buttons = [("Copy", copy)]
        if path is not None:
            buttons.append(("Open file", lambda: os.startfile(str(path))))
        self.bubble("jarvis", text, mono=(r.get("type") == "code"), buttons=buttons)

    def open_log(self):
        if UI_LOG.exists():
            subprocess.Popen(["notepad", str(UI_LOG)], creationflags=NOWIN)

    def open_report(self):
        if REPORT.exists():
            os.startfile(str(REPORT))

    def replay(self):
        if self.last_narration:
            self.speak(self.last_narration)

    # ---------- voice out ----------
    def is_speaking(self):
        now = time.time()
        if self.speak_pending:
            return True
        if now < self.audio_until:
            return True
        if self.speech_proc is not None:
            if self.speech_proc.poll() is not None:
                self.speech_proc = None
                return False
            return True
        return False

    def speak(self, text):
        self.stop_voice()
        self.speak_gen += 1
        self.speak_pending = True
        threading.Thread(target=self.speak_worker, args=(text, self.speak_gen), daemon=True).start()

    def speak_worker(self, text, gen):
        try:
            key = get_elevenlabs_key()
            if key:
                try:
                    self.tts_elevenlabs(text, key, gen)
                    log_ui("voice: ElevenLabs")
                    return
                except Exception as e:
                    log_ui(f"ElevenLabs failed ({e}); falling back to Windows voice")
            else:
                log_ui("voice: Windows (no valid ElevenLabs key)")
            if gen == self.speak_gen:
                self.tts_windows(text)
        finally:
            if gen == self.speak_gen:
                self.speak_pending = False

    def tts_elevenlabs(self, text, key, gen):
        voice = os.environ.get("ELEVENLABS_VOICE_ID", "").strip() or DEFAULT_VOICE_ID
        url = f"https://api.elevenlabs.io/v1/text-to-speech/{voice}?output_format=pcm_24000"
        body = json.dumps({"text": text, "model_id": ELEVEN_MODEL}).encode("utf-8")
        req = urllib.request.Request(url, data=body, method="POST",
                                     headers={"xi-api-key": key, "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                pcm = resp.read()
        except urllib.error.HTTPError as e:
            raise RuntimeError(f"HTTP {e.code}: {e.read().decode('utf-8', errors='replace')[:200]}")
        except urllib.error.URLError as e:
            raise RuntimeError(f"no connection: {e.reason}")
        if not pcm or winsound is None:
            raise RuntimeError("no audio to play")
        if gen != self.speak_gen:
            return
        with wave.open(str(NARRATION_WAV), "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(24000)
            wf.writeframes(pcm)
        if np is not None:
            self.play_pcm = np.frombuffer(pcm, dtype=np.int16).astype(np.float32) / 32768.0
        self.play_t0 = time.time()
        self.audio_until = self.play_t0 + len(pcm) / 2 / 24000.0
        winsound.PlaySound(str(NARRATION_WAV), winsound.SND_FILENAME | winsound.SND_ASYNC)

    def tts_windows(self, text):
        """Direct SAPI COM voice (instant, zero process linger) with powershell fallback."""
        def _run_sapi():
            # Estimate speech duration
            words = len(text.split())
            dur = max(1.5, words * 0.45)
            self.audio_until = time.time() + dur

            if COMTYPES_AVAILABLE:
                try:
                    import comtypes.client
                    voice = comtypes.client.CreateObject("SAPI.SpVoice")
                    voice.Speak(text)
                    self.audio_until = 0.0
                    return
                except Exception as e:
                    log_ui(f"SAPI COM failed: {e}")

            # Fallback to powershell
            OUT.mkdir(parents=True, exist_ok=True)
            SPEECH_FILE.write_text(text, encoding="utf-8")
            ps = ("Add-Type -AssemblyName System.Speech; "
                  "$s = New-Object System.Speech.Synthesis.SpeechSynthesizer; "
                  f"$s.Speak((Get-Content -Raw -Encoding UTF8 '{SPEECH_FILE}'))")
            proc = subprocess.Popen(["powershell", "-NoProfile", "-Command", ps], creationflags=NOWIN)
            self.speech_proc = proc
            try:
                proc.wait(timeout=dur + 5.0)
            except Exception:
                pass
            self.speech_proc = None
            self.audio_until = 0.0

        threading.Thread(target=_run_sapi, daemon=True).start()

    def stop_voice(self):
        self.speak_gen += 1
        self.speak_pending = False
        self.audio_until = 0.0
        self.play_pcm = None
        if winsound is not None:
            try:
                winsound.PlaySound(None, winsound.SND_PURGE)
            except Exception:
                pass
        if self.speech_proc and self.speech_proc.poll() is None:
            try:
                self.speech_proc.kill()
            except Exception:
                pass
        self.speech_proc = None

    # ---------- wake word listener ("Hey Jarvis" / "Hi Jarvis") ----------
    def start_wake_word_listener(self):
        if not VOSK_AVAILABLE or not VOSK_MODEL_PATH.exists():
            log_ui("Wake word unavailable (Vosk or model not found)")
            return
        t = threading.Thread(target=self._wake_word_worker, daemon=True)
        t.start()

    def _wake_word_worker(self):
        try:
            vosk.SetLogLevel(-1)
            model = vosk.Model(str(VOSK_MODEL_PATH))
            # Open vocabulary recognizer: robust against natural speech, accents, and surrounding words
            rec = vosk.KaldiRecognizer(model, 16000)
            rec.SetWords(True)
        except Exception as e:
            log_ui(f"wake word init error: {e}")
            return

        q = queue.Queue(maxsize=100)

        def callback(indata, frames, time_info, status):
            if self.wake_word_enabled and not self.recording:
                try:
                    q.put_nowait(bytes(indata))
                except queue.Full:
                    pass

        WAKE_WORDS = (
            "jarvis", "hey jarvis", "hi jarvis", "hello jarvis",
            "ok jarvis", "okay jarvis", "service", "travis", "javis", "harvest"
        )

        log_ui("Wake word listener active (say 'Hey Jarvis' or 'Hi Jarvis')")

        while self.wake_word_running:
            try:
                with sd.RawInputStream(samplerate=16000, blocksize=2000, dtype="int16", channels=1, callback=callback):
                    while self.wake_word_running:
                        try:
                            data = q.get(timeout=0.25)
                        except queue.Empty:
                            continue

                        # When speaking or recording or busy, ignore audio and reset buffer
                        if self.recording or self.is_speaking() or not self.wake_word_enabled:
                            while not q.empty():
                                try:
                                    q.get_nowait()
                                except queue.Empty:
                                    break
                            rec.Reset()
                            continue

                        matched = False
                        if rec.AcceptWaveform(data):
                            res = json.loads(rec.Result())
                            text = res.get("text", "").lower()
                        else:
                            part = json.loads(rec.PartialResult())
                            text = part.get("partial", "").lower()

                        if any(w in text for w in WAKE_WORDS):
                            matched = True

                        if matched:
                            log_ui(f"wake word detected: '{text}'")
                            rec.Reset()
                            while not q.empty():
                                try:
                                    q.get_nowait()
                                except queue.Empty:
                                    break
                            self.q.put(("wake_word", text))
                            time.sleep(1.2)  # Cooldown to avoid duplicate triggers
            except Exception as e:
                log_ui(f"wake word stream reconnecting: {e}")
                time.sleep(1.0)

    def on_wake_word_detected(self):
        if self.recording:
            return
        log_ui("Wake word triggered voice note recording")
        self.show_panel()
        if winsound is not None:
            try:
                winsound.MessageBeep(winsound.MB_OK)
            except Exception:
                pass
        self.toggle_record()

    # ---------- global hotkey (Ctrl+Alt+J) ----------
    def start_hotkey(self):
        if sys.platform != "win32":
            return
        self.hotkey_running = True
        self.hotkey_thread_id = None
        threading.Thread(target=self._hotkey_loop, daemon=True).start()

    def _hotkey_loop(self):
        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32
        self.hotkey_thread_id = kernel32.GetCurrentThreadId()

        MOD_ALT = 0x0001
        MOD_CONTROL = 0x0002
        MOD_NOREPEAT = 0x4000
        VK_J = 0x4A
        HOTKEY_ID = 101

        ok = user32.RegisterHotKey(None, HOTKEY_ID, MOD_CONTROL | MOD_ALT | MOD_NOREPEAT, VK_J)
        if not ok:
            ok = user32.RegisterHotKey(None, HOTKEY_ID, MOD_CONTROL | MOD_ALT, VK_J)
        if not ok:
            log_ui("could not register global hotkey Ctrl+Alt+J")
            return
        log_ui("global hotkey Ctrl+Alt+J active")

        class MSG(ctypes.Structure):
            _fields_ = [("hwnd", ctypes.c_void_p), ("message", ctypes.c_uint),
                        ("wParam", ctypes.c_size_t), ("lParam", ctypes.c_size_t),
                        ("time", ctypes.c_uint), ("pt", ctypes.c_long * 2)]
        msg = MSG()
        while self.hotkey_running:
            ret = user32.GetMessageW(ctypes.byref(msg), None, 0, 0)
            if ret == 0 or ret == -1:
                break
            if msg.message == 0x0312:
                self.root.after(0, self.toggle_panel)
        user32.UnregisterHotKey(None, HOTKEY_ID)

    def stop_hotkey(self):
        self.hotkey_running = False
        if self.hotkey_thread_id:
            try:
                ctypes.windll.user32.PostThreadMessageW(self.hotkey_thread_id, 0x0012, 0, 0)
            except Exception:
                pass

    def run(self):
        self.root.mainloop()


if __name__ == "__main__":
    Jarvis().run()