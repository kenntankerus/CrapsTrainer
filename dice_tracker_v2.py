"""
Craps Dice Tracker  (version 2: camera + manual)
------------------------------------------------
Log physical dice throws, tag each one with the dice set you used and where
you stood at the table (12 o'clock, stick left, stick right), and compare
sets, positions, or both together. Each user (the dropdown at top left) has
their own stats; "Add new user..." at the bottom of the list adds one.

  * Camera on:  throws are read from the webcam and logged automatically.
                Wrong read?  Type the right two digits (left die first, as in the
                camera picture) and press F2 (Fix last).
                Not a real throw?  Press Ctrl+Z to remove it.
                The camera's own reading is kept even after a fix, so the app can
                show how often the camera is right, and a small picture of each die
                is saved to the dice_crops folder. train_dice_model.py learns from them.
  * Camera off: type two digits (e.g. 34) and press Enter to log a throw.

Run:    python dice_tracker_v2.py            (or: python dice_tracker_v2.py 1   to use camera #1)
Needs:  Python 3 for the tracker itself.
        For the camera also:  pip install opencv-python numpy
        and dice_vision.py in the same folder as this file.
Data is saved automatically to dice_throws.csv next to this file.
"""

import csv
import hashlib
import json
import os
import queue
import subprocess
import sys
import threading
import time
import tkinter as tk
from collections import Counter
from datetime import datetime
from tkinter import ttk, messagebox, simpledialog

try:
    from dice_vision import DiceCamera
    import dice_vision
    CAMERA_IMPORT_ERROR = ""
except ImportError as error:           # opencv missing, or dice_vision.py not in this folder
    DiceCamera = None
    dice_vision = None
    CAMERA_IMPORT_ERROR = str(error)

try:
    import winsound                    # Windows only; used for a beep when the camera logs a throw
except ImportError:
    winsound = None

from dice_stats import (BONUS_SMALL, BONUS_TALL, COMPARE_MODES, DEFAULT_POSITIONS, DEFAULT_SETS, EXPECTED,
                        HARDWAY_SETUPS, RANDOM_NUMBERS_PER_RUN, UNSPECIFIED, bonus_status, parse_dice,
                        shooter_status, summarize, summarize_runs)
from stickman import DEFAULT_VOICE, MOODS, VOICES, croupier_call, pack_name, strip_tags, voice_lines

HERE = os.path.dirname(os.path.abspath(__file__))
DATA_FILE = os.path.join(HERE, "dice_throws.csv")
CROP_DIR = os.path.join(HERE, "dice_crops")      # one small picture per die, named <throw_id>_left/right.png
STATE_FILE = os.path.join(HERE, "tracker_state.json")      # the dice set, position and up-facing last used
FIELDS = ["timestamp", "session", "dice_set", "die1", "die2", "total", "back_wall", "source", "position",
          "camera_read", "throw_id", "user"]
FIRST_USER = "kenn"              # throws logged before there were users belong to this one
ADD_USER = "Add new user..."
# camera_read: what the camera said ("3-5"), "unclear" if it couldn't read the dice, "" for typed throws
UNCLEAR_WINDOW = 120             # seconds: a throw typed this soon after an unclear read gets its pictures
# ---------------------------------------------------------------------- look
FELT = "#0a281d"         # window background: dark table felt
CARD = "#123a2b"         # panels
INSET = "#0c2519"        # inputs, lists, chart
EDGE = "#2a6650"
BUTTON = "#1d5240"
BUTTON_HOVER = "#27684f"
TEXT = "#efe8d2"
MUTED = "#8fae9c"
GOLD = "#dcb64c"
GOLD_HOVER = "#ecc965"
RED = "#e05252"
DIE_RED = "#c23a32"
DIE_PIP = "#f6efdc"
UI_FONT = "Segoe UI"

PIP_SPOTS = {1: [(.5, .5)], 2: [(.27, .27), (.73, .73)], 3: [(.27, .27), (.5, .5), (.73, .73)],
             4: [(.27, .27), (.73, .27), (.27, .73), (.73, .73)],
             5: [(.27, .27), (.73, .27), (.5, .5), (.27, .73), (.73, .73)],
             6: [(.27, .25), (.27, .5), (.27, .75), (.73, .25), (.73, .5), (.73, .75)]}


def apply_theme(root):
    root.configure(bg=FELT)
    style = ttk.Style(root)
    style.theme_use("clam")
    style.configure(".", background=CARD, foreground=TEXT, fieldbackground=INSET, bordercolor=CARD,
                    lightcolor=CARD, darkcolor=CARD, troughcolor=INSET, focuscolor=CARD, font=(UI_FONT, 10))
    style.configure("Felt.TFrame", background=FELT)
    style.configure("TLabelframe", background=CARD, borderwidth=0, relief="flat")
    style.configure("TLabelframe.Label", background=CARD, foreground=GOLD, font=(UI_FONT, 9, "bold"))
    style.configure("Muted.TLabel", foreground=MUTED)

    style.configure("TButton", background=BUTTON, foreground=TEXT, borderwidth=0, relief="flat", padding=(12, 6))
    style.map("TButton", background=[("pressed", EDGE), ("active", BUTTON_HOVER)])
    style.configure("Gold.TButton", background=GOLD, foreground="#1a1405", font=(UI_FONT, 10, "bold"))
    style.map("Gold.TButton", background=[("pressed", GOLD), ("active", GOLD_HOVER)])

    style.configure("TCheckbutton", background=CARD, foreground=TEXT)
    style.map("TCheckbutton", background=[("active", CARD)], indicatorcolor=[("selected", GOLD), ("!selected", INSET)])
    style.configure("TEntry", fieldbackground=INSET, foreground=TEXT, insertcolor=TEXT, bordercolor=EDGE,
                    lightcolor=EDGE, darkcolor=EDGE, padding=4)
    style.configure("TCombobox", fieldbackground=INSET, background=BUTTON, foreground=TEXT, arrowcolor=GOLD,
                    bordercolor=EDGE, lightcolor=EDGE, darkcolor=EDGE, padding=3)
    style.map("TCombobox", fieldbackground=[("readonly", INSET)], foreground=[("readonly", TEXT)],
              selectbackground=[("readonly", INSET)], selectforeground=[("readonly", TEXT)],
              background=[("active", BUTTON_HOVER)])
    root.option_add("*TCombobox*Listbox.background", INSET)
    root.option_add("*TCombobox*Listbox.foreground", TEXT)
    root.option_add("*TCombobox*Listbox.selectBackground", GOLD)
    root.option_add("*TCombobox*Listbox.selectForeground", "#1a1405")

    style.configure("Treeview", background=INSET, fieldbackground=INSET, foreground=TEXT, rowheight=26,
                    borderwidth=0, relief="flat")
    style.map("Treeview", background=[("selected", BUTTON_HOVER)], foreground=[("selected", TEXT)])
    style.configure("Treeview.Heading", background=BUTTON, foreground=GOLD, relief="flat", borderwidth=0,
                    font=(UI_FONT, 9, "bold"), padding=(4, 6))
    style.map("Treeview.Heading", background=[("active", BUTTON_HOVER)])

    # Dark title bar on Windows 10/11; harmless if it isn't available
    try:
        import ctypes
        root.update()
        hwnd = ctypes.windll.user32.GetParent(root.winfo_id())
        ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, 20, ctypes.byref(ctypes.c_int(1)), 4)
    except Exception:
        pass


def draw_die(canvas, x, y, size, value):
    """A casino-red die with ivory pips (value 0 draws a blank die)."""
    r = size * 0.18
    canvas.create_polygon(_rounded(x + 2, y + 3, x + size + 2, y + size + 3, r), smooth=True, fill="#05140e", outline="")
    canvas.create_polygon(_rounded(x, y, x + size, y + size, r), smooth=True, fill=DIE_RED, outline="#8f2a24", width=1)
    pip = size * 0.085
    for px, py in PIP_SPOTS.get(value, []):
        cx, cy = x + px * size, y + py * size
        canvas.create_oval(cx - pip, cy - pip, cx + pip, cy + pip, fill=DIE_PIP, outline="")


def _rounded(x0, y0, x1, y1, r):
    return [x0 + r, y0, x1 - r, y0, x1, y0, x1, y0 + r, x1, y1 - r, x1, y1, x1 - r, y1,
            x0 + r, y1, x0, y1, x0, y1 - r, x0, y0 + r, x0, y0]






PACK_DIR = os.path.join(HERE, "voice_packs")      # recorded lines, made by make_voice_pack.py


def pack_folder(voice):
    return os.path.join(PACK_DIR, pack_name(voice))


class Speaker:
    """Speaks the stickman's lines with an edge-tts voice (needs internet the first time a line is used) and plays
    them through Windows' own audio. Clips are kept in voice_cache, and prepare() makes every line ahead of time,
    so calls play the moment the dice settle. Without edge-tts or the network it falls back to the Windows voice.
    A line recorded in the voice's pack (voice_packs/<voice>/<line>.mp3) is played instead of being made."""
    FALLBACK = ("Add-Type -AssemblyName System.Speech; $s = New-Object System.Speech.Synthesis.SpeechSynthesizer; "
                "while (($l = [Console]::In.ReadLine()) -ne $null) { $s.SpeakAsyncCancelAll(); $s.SpeakAsync($l) | Out-Null }")

    def __init__(self):
        self.cache_dir = os.path.join(HERE, "voice_cache")
        self.latest = 0
        self.lock = threading.Lock()
        self.process = None
        self.prepared = set()

    def _clip(self, text, mood, voice):
        """(path, edge-tts arguments) for one line."""
        name, rate_shift, pitch_shift, bend = VOICES[voice]
        rate, pitch, volume = MOODS[mood]
        rate, pitch, volume = f"{rate + rate_shift:+d}%", f"{pitch + pitch_shift:+d}Hz", f"{volume:+d}%"
        text = strip_tags(text)
        if bend:
            text = bend(text)
        key = f"{name}{rate}{pitch}{volume}{text}"
        path = os.path.join(self.cache_dir, hashlib.md5(key.encode()).hexdigest() + ".mp3")
        return path, (text, name, rate, pitch, volume)

    async def _make(self, path, args):
        import edge_tts
        text, name, rate, pitch, volume = args
        part = f"{path}.{threading.get_ident()}.part"      # two threads may make the same clip at once
        await edge_tts.Communicate(text, name, rate=rate, pitch=pitch, volume=volume).save(part)
        os.replace(part, path)

    def prepare(self, voice):
        """Make every line in this voice in the background, a few at a time."""
        if voice not in VOICES or voice in self.prepared:
            return
        self.prepared.add(voice)

        async def make_all():
            import asyncio
            gate = asyncio.Semaphore(4)
            pack = pack_folder(voice)
            missing = [clip for key, (variants, mood) in voice_lines(voice).items() for i, text in enumerate(variants)
                       if not os.path.exists(os.path.join(pack, f"{key}_{i}.mp3"))
                       for clip in [self._clip(text, mood, voice)] if not os.path.exists(clip[0])]

            async def one(path, args):
                async with gate:
                    try:
                        await self._make(path, args)
                    except Exception:
                        pass
            await asyncio.gather(*(one(path, args) for path, args in missing))

        def run():
            import asyncio
            try:
                os.makedirs(self.cache_dir, exist_ok=True)
                asyncio.run(make_all())
            except Exception:
                self.prepared.discard(voice)
        threading.Thread(target=run, daemon=True).start()

    def say(self, pieces, voice=DEFAULT_VOICE):
        self.latest += 1
        threading.Thread(target=self._speak, args=(pieces, voice, self.latest), daemon=True).start()

    def _speak(self, pieces, voice, ticket):
        try:
            import asyncio
            paths = []
            for text, mood, name in pieces:
                recorded = os.path.join(pack_folder(voice), name + ".mp3")
                if os.path.exists(recorded):
                    paths.append(recorded)
                    continue
                path, args = self._clip(text, mood, voice)
                if not os.path.exists(path):
                    os.makedirs(self.cache_dir, exist_ok=True)
                    asyncio.run(self._make(path, args))
                paths.append(path)
        except Exception:
            if ticket == self.latest:
                self._fallback(" ".join(strip_tags(piece[0]) for piece in pieces))
            return
        self._play_all(paths, ticket)

    def _play_all(self, paths, ticket):
        """Play the clips in order. A newer call (a higher ticket) cuts this one off."""
        import ctypes
        mci = ctypes.windll.winmm.mciSendStringW
        mode = ctypes.create_unicode_buffer(32)
        for path in paths:
            with self.lock:
                if ticket != self.latest:
                    return
                mci("close croupier", None, 0, None)
                if mci(f'open "{path}" type mpegvideo alias croupier', None, 0, None) != 0:
                    continue
                mci("play croupier", None, 0, None)
            while True:
                time.sleep(0.03)
                with self.lock:
                    if ticket != self.latest:
                        return
                    mci("status croupier mode", mode, 32, None)
                    if mode.value != "playing":
                        break

    def _fallback(self, text):
        try:
            if self.process is None or self.process.poll() is not None:
                self.process = subprocess.Popen(
                    ["powershell", "-NoProfile", "-Command", self.FALLBACK], stdin=subprocess.PIPE,
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, text=True,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            self.process.stdin.write(text.replace("\n", " ") + "\n")
            self.process.stdin.flush()
        except (OSError, ValueError):
            self.process = None

    def stop(self):
        if self.process is not None:
            self.process.terminate()
            self.process = None


def _code_timestamp():
    # Newest save time among the program files and settings, so a stale window is easy to spot
    here = os.path.dirname(os.path.abspath(__file__))
    names = ("dice_tracker_v2.py", "dice_vision.py", "dice_settings.json")
    newest = max(os.path.getmtime(os.path.join(here, n)) for n in names if os.path.exists(os.path.join(here, n)))
    return datetime.fromtimestamp(newest).strftime("%Y-%m-%d %H:%M:%S")


CAMERA_INDEX = int(sys.argv[1]) if len(sys.argv) > 1 and sys.argv[1].isdigit() else 1    # other scripts import this file



def upgrade_data_file():
    """Rewrite an older dice_throws.csv with today's columns, so rows appended to it line up."""
    if not os.path.exists(DATA_FILE):
        return
    with open(DATA_FILE, newline="") as f:
        reader = csv.DictReader(f)
        if all(field in (reader.fieldnames or []) for field in FIELDS):
            return
        rows = list(reader)
    for row in rows:
        row["user"] = row.get("user") or FIRST_USER
    with open(DATA_FILE, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def known_users(state, throws=()):
    users = [FIRST_USER] + list(state.get("users") or []) + [t["user"] for t in throws]
    return list(dict.fromkeys(u for u in users if u))


def update_state(**values):
    """Save settings to the state file shared with craps_game.py, keeping the ones the other app wrote."""
    try:
        with open(STATE_FILE) as f:
            state = json.load(f)
    except (OSError, ValueError):
        state = {}
    if not isinstance(state, dict):
        state = {}
    state.update(values)
    try:
        with open(STATE_FILE, "w") as f:
            json.dump(state, f, indent=2)
    except OSError:
        pass


class UserPicker:
    """The user dropdown. Picking a name calls on_change(name); the last entry adds a new user."""

    def __init__(self, parent, users, current, on_change):
        self.users = list(users)
        self.current = current if current in self.users else self.users[0]
        self.on_change = on_change
        self.var = tk.StringVar(value=self.current)
        self.box = ttk.Combobox(parent, textvariable=self.var, values=self.users + [ADD_USER], width=12,
                                state="readonly")
        self.box.bind("<<ComboboxSelected>>", lambda e: self.picked())

    def picked(self):
        name = self.var.get()
        if name == ADD_USER:
            typed = (simpledialog.askstring("New user", "Name for the new user:", parent=self.box) or "").strip()
            name = next((u for u in self.users if u.lower() == typed.lower()), typed)
            if not name or name == ADD_USER:
                self.var.set(self.current)
                return
            if name not in self.users:
                self.users.append(name)
                self.box.configure(values=self.users + [ADD_USER])
            self.var.set(name)
        if name != self.current:
            self.current = name
            self.on_change(name)


# ----------------------------------------------------------------------- app
class DiceTracker:
    def __init__(self, root):
        self.root = root
        apply_theme(root)
        root.title(f"Craps Dice Tracker - code from {_code_timestamp()}")
        root.state("zoomed")
        self.throws = []                 # the current user's throws: everything on screen, undo and F2 use these
        self.all_throws = self.load_throws()
        self.session = datetime.now().strftime("%Y-%m-%d")
        self.camera = None
        self._last_preview = None
        self._warned_resolution = False
        self.preview_image = None
        self.pending_unclear = None      # (time, crops) from the last read the camera couldn't make

        self.build_controls()
        self.throws = [t for t in self.all_throws if t["user"] == self.user_picker.current]
        self.bottom_frame = bottom = ttk.Frame(root, style="Felt.TFrame")                 # stats table on the left, chart on the right
        bottom.pack(side="bottom", fill="x", padx=12, pady=(6, 12))    # packed first so the chart is never squeezed out
        self.build_middle()
        self.build_stats_table(bottom)
        self.build_chart(bottom)
        self.refresh()
        self._fit_job = None
        root.bind("<Configure>", self.schedule_fit)

        root.bind("<Control-z>", lambda event: self.undo())
        root.bind("<F2>", lambda event: self.fix_last())
        root.protocol("WM_DELETE_WINDOW", self.on_close)
        self.dice_entry.focus_set()
        self.poll_camera()

    # ------------------------------------------------------------- building UI
    def build_controls(self):
        self.controls_frame = frame = ttk.Frame(self.root, padding=10)
        frame.pack(fill="x", padx=12, pady=(8, 4))

        state = self.load_state()
        ttk.Label(frame, text="User:").grid(row=0, column=0, sticky="w")
        self.user_picker = UserPicker(frame, known_users(state, self.all_throws), state.get("user"), self.switch_user)
        self.user_picker.box.grid(row=0, column=1, padx=(5, 10))

        # Dice set and table position: dropdowns you can also type a new name into
        ttk.Label(frame, text="Dice set:").grid(row=0, column=2, sticky="w")
        known_sets = list(DEFAULT_SETS)
        for t in self.all_throws:
            if t["dice_set"] not in known_sets:
                known_sets.append(t["dice_set"])
        if state.get("dice_set") and state["dice_set"] not in known_sets:
            known_sets.append(state["dice_set"])
        self.set_var = tk.StringVar(value=state.get("dice_set") or known_sets[0])
        self.set_box = ttk.Combobox(frame, textvariable=self.set_var, values=known_sets, width=14)
        self.set_box.grid(row=0, column=3, padx=5)
        self.set_box.bind("<<ComboboxSelected>>", lambda e: self.after_set_change())
        self.set_box.bind("<Return>", lambda e: self.after_set_change())

        ttk.Label(frame, text="Position:").grid(row=0, column=4, padx=(10, 0), sticky="w")
        known_positions = list(DEFAULT_POSITIONS)
        for t in self.all_throws:
            if t["position"] not in known_positions and t["position"] != UNSPECIFIED:
                known_positions.append(t["position"])
        if state.get("position") and state["position"] not in known_positions:
            known_positions.append(state["position"])
        self.position_var = tk.StringVar(value=state.get("position") or known_positions[0])
        self.position_box = ttk.Combobox(frame, textvariable=self.position_var, values=known_positions, width=11)
        self.position_box.grid(row=0, column=5, padx=5)
        self.position_box.bind("<<ComboboxSelected>>", lambda e: self.after_set_change())
        self.position_box.bind("<Return>", lambda e: self.after_set_change())

        # Dice entry: type two digits, e.g. 34, then press Enter
        ttk.Label(frame, text="Dice (e.g. 34):").grid(row=0, column=6, padx=(10, 0))
        self.dice_var = tk.StringVar()
        self.dice_entry = ttk.Entry(frame, textvariable=self.dice_var, width=6, font=(UI_FONT, 14))
        self.dice_entry.grid(row=0, column=7, padx=5)
        self.dice_entry.bind("<Return>", lambda e: self.log_manual())

        ttk.Button(frame, text="Log new", style="Gold.TButton", command=self.log_manual).grid(row=0, column=8, padx=2)
        ttk.Button(frame, text="Fix last (F2)", command=self.fix_last).grid(row=0, column=9, padx=2)
        ttk.Button(frame, text="Undo (Ctrl+Z)", command=self.undo).grid(row=0, column=10, padx=2)

        # Camera controls, top right
        cam = ttk.Frame(frame)
        cam.grid(row=0, column=11, rowspan=4, sticky="nw", padx=(25, 0))
        frame.columnconfigure(11, weight=1)
        self.camera_button = ttk.Button(cam, text="Start camera", style="Gold.TButton", command=self.toggle_camera)
        self.camera_button.grid(row=0, column=0, sticky="w")
        self.camera_status_var = tk.StringVar(value="Camera off")
        ttk.Label(cam, textvariable=self.camera_status_var).grid(row=0, column=1, sticky="w", padx=8)
        self.back_wall_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(cam, text="Hit back wall", variable=self.back_wall_var).grid(
            row=1, column=0, sticky="w", pady=(8, 0))
        voice_row = ttk.Frame(cam)
        voice_row.grid(row=1, column=1, sticky="w", padx=8, pady=(8, 0))
        ttk.Label(voice_row, text="Croupier:").pack(side="left")
        voices = ["Off"] + list(VOICES)
        self.voice_var = tk.StringVar(value=state.get("voice") if state.get("voice") in voices else DEFAULT_VOICE)
        voice_box = ttk.Combobox(voice_row, textvariable=self.voice_var, values=voices, width=18, state="readonly")
        voice_box.pack(side="left", padx=5)
        voice_box.bind("<<ComboboxSelected>>", lambda e: (self.save_state(), self.speaker.prepare(self.voice_var.get())))
        self.speaker = Speaker()
        self.speaker.prepare(self.voice_var.get())
        self.hint_label = ttk.Label(cam, text="Camera wrong? Type the right digits (left die first), then F2.",
                                    style="Muted.TLabel", wraplength=300)
        self.hint_label.grid(row=2, column=0, columnspan=2, sticky="w", pady=(8, 0))

        # Only for Hardway sets: how the dice are held. It takes the hint's place while it is showing.
        self.setup_frame = ttk.Frame(cam)
        ttk.Label(self.setup_frame, text="Up-facing:").pack(side="left")
        self.setup_var = tk.StringVar(value=state.get("setup", "") if state.get("setup", "") in HARDWAY_SETUPS else "")
        self.setup_box = ttk.Combobox(self.setup_frame, textvariable=self.setup_var, values=[""] + HARDWAY_SETUPS,
                                      width=5, state="readonly")
        self.setup_box.pack(side="left", padx=5)
        self.setup_box.bind("<<ComboboxSelected>>", lambda e: self.after_set_change())
        ttk.Label(self.setup_frame, text="(e.g. 6-2 = sixes up, twos toward you)", style="Muted.TLabel",
                  wraplength=170).pack(side="left")
        self.set_var.trace_add("write", lambda *_: self.show_setup_box())
        self.show_setup_box()

        # The last throw, big enough to read from a distance
        self.last_var = tk.StringVar(value="No throws yet")
        last_row = ttk.Frame(frame)
        last_row.grid(row=2, column=0, columnspan=11, sticky="w", pady=(12, 0))
        self.dice_canvas = tk.Canvas(last_row, width=138, height=64, bg=CARD, highlightthickness=0)
        self.dice_canvas.pack(side="left", padx=(0, 16))
        ttk.Label(last_row, textvariable=self.last_var, font=(UI_FONT, 20, "bold"), foreground=GOLD).pack(side="left")
        self.draw_last_dice(0, 0)

        self.status_var = tk.StringVar()
        ttk.Label(frame, textvariable=self.status_var, style="Muted.TLabel").grid(
            row=3, column=0, columnspan=11, sticky="w", pady=(4, 0))

    def build_bonus(self, middle):
        panel = ttk.LabelFrame(middle, text="Small / Tall / All", padding=10)
        panel.pack(side="left", fill="both", expand=True, padx=(10, 0))
        # The puck and the shooter's numbers
        shooter = ttk.Frame(panel)
        shooter.pack(fill="x")
        self.puck_canvas = tk.Canvas(shooter, width=72, height=72, bg=CARD, highlightthickness=0)
        self.puck_canvas.grid(row=0, column=0, padx=(0, 10))
        self.shooter_vars = {}
        for column, (key, caption) in enumerate((("point", "POINT"), ("run", "RUN"), ("last", "LAST RUN")), start=1):
            shooter.columnconfigure(column, weight=1)
            block = ttk.Frame(shooter)
            block.grid(row=0, column=column)
            self.shooter_vars[key] = tk.StringVar()
            ttk.Label(block, textvariable=self.shooter_vars[key], font=(UI_FONT, 28, "bold"), foreground=GOLD).pack()
            ttk.Label(block, text=caption, style="Muted.TLabel", font=(UI_FONT, 8, "bold")).pack()

        self.chip_canvas = tk.Canvas(panel, height=104, bg=CARD, highlightthickness=0)
        self.chip_canvas.pack(fill="x", pady=(10, 0))
        self.chip_state = (set(), False, False)
        self.chip_canvas.bind("<Configure>", lambda e: self.draw_chips())
        self.bonus_var = tk.StringVar()
        ttk.Label(panel, textvariable=self.bonus_var, style="Muted.TLabel").pack(anchor="w")
        ttk.Label(panel, text="Recent wins, with the dice sets thrown in that run:", style="Muted.TLabel").pack(
            anchor="w", pady=(6, 0))
        self.bonus_wins = self.make_listbox(panel, height=3, size=9)
        self.bonus_wins.pack(fill="x")

    def build_middle(self):
        middle = ttk.Frame(self.root, style="Felt.TFrame")
        middle.pack(fill="x", padx=12, pady=4)

        recent_frame = ttk.LabelFrame(middle, text="RECENT THROWS", padding=10)
        recent_frame.pack(side="left", fill="y")
        self.recent_list = self.make_listbox(recent_frame, height=6, size=10)
        self.recent_list.configure(width=60)
        self.recent_list.pack(fill="both", expand=True)
        self.build_bonus(middle)

        camera_frame = ttk.LabelFrame(middle, text="CAMERA", padding=10)
        camera_frame.pack(side="right", padx=(10, 0))
        self.blank_preview = tk.PhotoImage(width=480, height=270)
        self.preview_label = tk.Label(camera_frame, text="Camera off", image=self.blank_preview, compound="center",
                                      bg=INSET, fg=MUTED, bd=0)
        self.preview_label.pack()
        self.accuracy_var = tk.StringVar()
        ttk.Label(camera_frame, textvariable=self.accuracy_var, style="Muted.TLabel",
                  wraplength=480).pack(anchor="w", pady=(4, 0))

    def build_stats_table(self, parent):
        title = ttk.Frame(parent)
        ttk.Label(title, text="COMPARED BY", font=(UI_FONT, 9, "bold"), foreground=GOLD).pack(side="left")
        self.compare_var = tk.StringVar(value=COMPARE_MODES[0])
        compare_box = ttk.Combobox(title, textvariable=self.compare_var, values=COMPARE_MODES,
                                   width=14, state="readonly")
        compare_box.pack(side="left", padx=8)
        compare_box.bind("<<ComboboxSelected>>", lambda e: self.after_set_change())
        ttk.Label(title, text=f"fair dice: 16.7% sevens · 6.0 rolls per seven · {RANDOM_NUMBERS_PER_RUN:.1f} numbers per run",
                  style="Muted.TLabel").pack(side="left")
        frame = ttk.LabelFrame(parent, labelwidget=title, padding=(10, 6))
        frame.pack(side="left", fill="both", expand=True, padx=(0, 12))

        columns = ("group", "throws", "sevens", "range", "rps", "hardways", "verdict",
                   "runs", "numbers", "bonus", "numbers_verdict")
        headings = ("Group", "Throws", "Sevens", "95% range", "Rolls/7", "Hardways", "vs random",
                    "Runs", "Nums/run", "S/T/A", "Nums vs random")
        widths = (120, 48, 54, 84, 54, 62, 96, 42, 62, 56, 100)
        self.stats_tree = ttk.Treeview(frame, columns=columns, show="headings", height=5)
        for col, heading, width in zip(columns, headings, widths):
            self.stats_tree.heading(col, text=heading)
            self.stats_tree.column(col, width=width, anchor="center")
        self.stats_tree.pack(fill="both", expand=True)

    def build_chart(self, parent):
        frame = ttk.LabelFrame(
            parent, text="TOTALS FOR THE CURRENT SELECTION   bars = yours · red line = fair dice", padding=10)
        frame.pack(side="right")
        self.canvas = tk.Canvas(frame, width=490, height=185, bg=INSET, highlightthickness=0)
        self.canvas.pack()

    # --------------------------------------------------------------- the actions
    def add_throw(self, die1, die2, source, camera_read="", crops=None):
        self.save_state()
        throw_id = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
        if crops and len(crops) == 2:
            self.save_crops(throw_id, crops)
        throw = {
            "timestamp": datetime.now().isoformat(timespec="seconds"),
            "session": self.session,
            "dice_set": self.current_set(),
            "position": self.position_var.get().strip() or UNSPECIFIED,
            "die1": die1,
            "die2": die2,
            "total": die1 + die2,
            "back_wall": self.back_wall_var.get(),
            "source": source,
            "camera_read": camera_read,
            "throw_id": throw_id,
            "user": self.user_picker.current,
        }
        self.throws.append(throw)
        self.all_throws.append(throw)
        self.append_to_file(throw)
        self.show_last(throw)
        self.refresh()
        self.speak_call()
        return throw

    def log_manual(self):
        dice = parse_dice(self.dice_var.get())
        if dice is None:
            self.status_var.set("Type exactly two dice, each 1-6 (for example 34).")
            self.dice_entry.selection_range(0, "end")
            return
        camera_read, crops = "", None
        if self.pending_unclear and time.time() - self.pending_unclear[0] < UNCLEAR_WINDOW:
            camera_read, crops = "unclear", self.pending_unclear[1]      # the throw the camera missed
        self.pending_unclear = None
        throw = self.add_throw(dice[0], dice[1], "manual", camera_read, crops)
        self.dice_var.set("")
        self.status_var.set(f"Logged {dice[0]}-{dice[1]} on {throw['dice_set']} ({throw['position']})")
        self.dice_entry.focus_set()

    def fix_last(self):
        """Replace the dice on the most recent throw with what's typed in the box."""
        if not self.throws:
            self.status_var.set("Nothing to fix yet.")
            return
        dice = parse_dice(self.dice_var.get())
        if dice is None:
            self.status_var.set("To fix the last throw, type the two correct dice (like 34) and press F2.")
            self.dice_entry.focus_set()
            return
        last = self.throws[-1]
        old = f"{last['die1']}-{last['die2']}"
        last["die1"], last["die2"] = dice
        last["total"] = dice[0] + dice[1]
        last["source"] = "corrected"
        self.rewrite_file()
        self.dice_var.set("")
        self.show_last(last)
        self.status_var.set(f"Fixed last throw: {old} -> {dice[0]}-{dice[1]}")
        self.refresh()
        self.speak_call()
        self.dice_entry.focus_set()

    def undo(self):
        if not self.throws:
            return
        removed = self.throws.pop()
        self.all_throws = [t for t in self.all_throws if t is not removed]
        self.delete_crops(removed.get("throw_id", ""))
        self.rewrite_file()
        self.status_var.set(f"Removed {removed['die1']}-{removed['die2']} ({removed['dice_set']})")
        if self.throws:
            self.show_last(self.throws[-1])
        else:
            self.last_var.set("No throws yet")
            self.draw_last_dice(0, 0)
        self.refresh()

    def schedule_fit(self, event):
        if event.widget is self.root:
            if self._fit_job:
                self.root.after_cancel(self._fit_job)
            self._fit_job = self.root.after(150, self.fit_preview)

    def fit_preview(self):
        """Size the camera picture to the room left once the controls and bottom panels are placed."""
        left = self.root.winfo_height() - self.controls_frame.winfo_reqheight() - self.bottom_frame.winfo_reqheight() - 104
        width = max(240, min(640, int(left * 16 / 9)))
        if width == self.blank_preview.width():
            return
        self.blank_preview = tk.PhotoImage(width=width, height=width * 9 // 16)
        if dice_vision is not None:
            dice_vision.PREVIEW_WIDTH = width
        if self.camera is None:
            self.preview_label.configure(image=self.blank_preview)

    def make_listbox(self, parent, height, size):
        box = tk.Listbox(parent, height=height, font=("Consolas", size), bg=INSET, fg=TEXT, bd=0,
                         highlightthickness=0, selectbackground=BUTTON_HOVER, selectforeground=TEXT,
                         activestyle="none")
        box.pack(fill="x" if height < 6 else "both", expand=height >= 6)
        return box

    def draw_last_dice(self, die1, die2):
        self.dice_canvas.delete("all")
        draw_die(self.dice_canvas, 2, 2, 56, die1)
        draw_die(self.dice_canvas, 76, 2, 56, die2)

    def current_set(self):
        name = self.set_var.get().strip() or "Unnamed"
        setup = self.setup_var.get()
        return f"{name} {setup}" if name == "Hardway" and setup else name

    def show_setup_box(self):
        if self.set_var.get().strip() == "Hardway":
            self.hint_label.grid_remove()
            self.setup_frame.grid(row=2, column=0, columnspan=2, sticky="w", pady=(8, 0))
        else:
            self.setup_frame.grid_remove()
            self.hint_label.grid()

    def load_state(self):
        try:
            with open(STATE_FILE) as f:
                state = json.load(f)
            return state if isinstance(state, dict) else {}
        except (OSError, ValueError):
            return {}

    def save_state(self):
        update_state(dice_set=self.set_var.get().strip(), setup=self.setup_var.get(),
                     position=self.position_var.get().strip(), voice=self.voice_var.get(),
                     user=self.user_picker.current, users=self.user_picker.users)

    def switch_user(self, name):
        self.throws = [t for t in self.all_throws if t["user"] == name]
        self.pending_unclear = None          # a missed throw's pictures belong to whoever was throwing
        self.save_state()
        if self.throws:
            self.show_last(self.throws[-1])
        else:
            self.last_var.set("No throws yet")
            self.draw_last_dice(0, 0)
        self.status_var.set(f"Logging throws for {name}.")
        self.refresh()
        self.dice_entry.focus_set()

    def after_set_change(self):
        self.save_state()
        self.refresh()
        self.dice_entry.focus_set()

    def show_last(self, throw):
        self.draw_last_dice(throw["die1"], throw["die2"])
        self.last_var.set(f"Last: {throw['die1']}-{throw['die2']}  =  {throw['total']}"
                          f"     ({throw['source']}, {throw['dice_set']}, {throw['position']})")

    # ------------------------------------------------------------------- camera
    def toggle_camera(self):
        if self.camera is not None:
            self.stop_camera()
            return
        if DiceCamera is None:
            messagebox.showinfo(
                "Camera not available",
                "The camera needs OpenCV and the dice_vision.py file next to this one.\n\n"
                "Install with:  pip install opencv-python numpy\n\n"
                f"Details: {CAMERA_IMPORT_ERROR}")
            return
        self.camera_status_var.set("Starting camera...")
        self.root.update_idletasks()
        camera = DiceCamera(CAMERA_INDEX)
        if not camera.start():
            self.camera_status_var.set("Camera off")
            messagebox.showerror("Camera", camera.error)
            return
        self.camera = camera
        self.camera_button.configure(text="Stop camera")
        self.status_var.set("Camera on. Throws will be logged automatically.")

    def stop_camera(self):
        if self.camera is not None:
            self.camera.stop()
            self.camera = None
        self.camera_button.configure(text="Start camera")
        self.camera_status_var.set("Camera off")
        self.preview_image = None
        self._last_preview = None
        self._warned_resolution = False
        self.preview_label.configure(image=self.blank_preview, text="Camera off")

    def beep(self, ok=True):
        try:
            if winsound is not None:
                winsound.Beep(1000 if ok else 400, 70)
            else:
                self.root.bell()
        except Exception:
            pass

    def poll_camera(self):
        """Runs every 100 ms: pick up anything the camera found and update the picture."""
        camera = self.camera
        if camera is not None:
            while True:
                try:
                    kind, payload = camera.events.get_nowait()
                except queue.Empty:
                    break
                if kind == "throw":
                    die1, die2 = payload["dice"]
                    self.pending_unclear = None
                    self.add_throw(die1, die2, "camera", f"{die1}-{die2}", payload.get("crops"))
                    self.status_var.set(f"Camera logged {die1}-{die2}. Wrong? Type the right digits "
                                        "(left die first) and press F2.")
                    self.beep(True)
                elif kind == "unclear":
                    self.pending_unclear = (time.time(), payload.get("crops") or [])
                    self.status_var.set(payload["message"])
                    self.beep(False)
                elif kind == "error":
                    self.stop_camera()
                    messagebox.showerror("Camera", str(payload))
                    camera = None
                    break
            if camera is not None:
                resolution = getattr(camera, "resolution", None)
                if resolution:
                    fps = f" @ {camera.fps:.0f} fps" if camera.fps else ""
                    self.camera_status_var.set(f"Camera {resolution[0]}x{resolution[1]}{fps}: {camera.status}")
                    if resolution[0] < 1280 and not self._warned_resolution:
                        self._warned_resolution = True
                        self.status_var.set(
                            f"The camera is only giving {resolution[0]}x{resolution[1]}, so pips will be tiny. "
                            "Try a USB 3 port, or close other apps using the camera.")
                else:
                    self.camera_status_var.set(f"Camera: {camera.status}")
                png = camera.get_preview_png()
                if png is not None and png is not self._last_preview:
                    self._last_preview = png
                    self.preview_image = tk.PhotoImage(data=png)
                    self.preview_label.configure(image=self.preview_image, text="", width=0, height=0)
        self.root.after(100, self.poll_camera)

    def on_close(self):
        self.save_state()
        self.speaker.stop()
        if self.camera is not None:
            self.camera.stop()
            self.camera = None
        self.root.destroy()

    # ------------------------------------------------------------ saving/loading
    def load_throws(self):
        if not os.path.exists(DATA_FILE):
            return []
        throws = []
        with open(DATA_FILE, newline="") as f:
            reader = csv.DictReader(f)
            columns = reader.fieldnames or []
            old_format = any(field not in columns for field in FIELDS)
            for row in reader:
                throws.append({
                    "timestamp": row["timestamp"],
                    "session": row["session"],
                    "dice_set": row["dice_set"],
                    "die1": int(row["die1"]),
                    "die2": int(row["die2"]),
                    "total": int(row["total"]),
                    "back_wall": row["back_wall"] == "True",
                    "source": row.get("source") or "manual",
                    "position": row.get("position") or UNSPECIFIED,
                    "camera_read": row.get("camera_read") or "",
                    "throw_id": row.get("throw_id") or "",
                    "user": row.get("user") or FIRST_USER,
                })
        if old_format:                       # older file: add the new columns
            self.all_throws = throws
            self.rewrite_file()
        return throws

    def append_to_file(self, throw):
        is_new = not os.path.exists(DATA_FILE)
        try:
            with open(DATA_FILE, "a", newline="") as f:
                writer = csv.DictWriter(f, fieldnames=FIELDS)
                if is_new:
                    writer.writeheader()
                writer.writerow(throw)
        except OSError as error:
            messagebox.showerror("Could not save", str(error))

    def rewrite_file(self):
        try:
            with open(DATA_FILE, "w", newline="") as f:
                writer = csv.DictWriter(f, fieldnames=FIELDS)
                writer.writeheader()
                writer.writerows(self.all_throws)
        except OSError as error:
            messagebox.showerror("Could not save", str(error))

    def save_crops(self, throw_id, crops):
        try:
            os.makedirs(CROP_DIR, exist_ok=True)
            for side, png in zip(("left", "right"), crops):
                with open(os.path.join(CROP_DIR, f"{throw_id}_{side}.png"), "wb") as f:
                    f.write(png)
        except OSError as error:
            self.status_var.set(f"Couldn't save the dice pictures: {error}")

    def delete_crops(self, throw_id):
        if not throw_id:
            return
        for side in ("left", "right"):
            try:
                os.remove(os.path.join(CROP_DIR, f"{throw_id}_{side}.png"))
            except OSError:
                pass

    # ------------------------------------------------------------------ display
    def refresh(self):
        self.refresh_bonus()
        self.refresh_accuracy()
        self.refresh_recent()
        self.refresh_stats()
        self.draw_chart()

    def speak_call(self):
        if self.voice_var.get() in VOICES:
            self.speaker.say(croupier_call(self.throws, self.voice_var.get()), voice=self.voice_var.get())

    def refresh_bonus(self):
        hit, run, small_done, tall_done, wins, _ = bonus_status(self.throws)

        self.chip_state = (hit, small_done, tall_done)
        self.draw_chips()

        made = Counter(w["kind"] for w in wins)
        self.bonus_var.set(f"Rolls this run: {run}     Made:  Small {made['Small']} · Tall {made['Tall']} · All {made['All']}")

        point, shooter_rolls, last_run = shooter_status(self.throws)
        self.shooter_vars["point"].set(str(point) if point else "—")
        self.shooter_vars["run"].set(str(shooter_rolls))
        self.shooter_vars["last"].set(str(last_run) if last_run is not None else "—")
        puck = self.puck_canvas
        puck.delete("all")
        if point:
            puck.create_oval(4, 4, 68, 68, fill="#f4f1e6", outline=GOLD, width=3)
            puck.create_text(36, 36, text="ON", fill="#111111", font=(UI_FONT, 20, "bold"))
        else:
            puck.create_oval(4, 4, 68, 68, fill="#111111", outline=EDGE, width=3)
            puck.create_text(36, 36, text="OFF", fill="#f4f1e6", font=(UI_FONT, 18, "bold"))

        self.bonus_wins.delete(0, "end")
        for w in reversed(wins[-6:]):
            used = ", ".join(f"{name} x{n}" for name, n in w["sets"].most_common())
            self.bonus_wins.insert("end", f"{w['time'][5:16].replace('T', ' ')}  {w['kind']:<5} {w['rolls']:>2} rolls  {used}")

    def draw_chips(self):
        hit, small_done, tall_done = self.chip_state
        canvas = self.chip_canvas
        canvas.delete("all")
        width = max(canvas.winfo_width(), 420)
        step = (width - 150) / 5
        for row_index, (name, numbers, done) in enumerate(
                (("SMALL", BONUS_SMALL, small_done), ("TALL", BONUS_TALL, tall_done))):
            y = 26 + row_index * 52
            canvas.create_text(0, y, text=name, anchor="w", fill=GOLD if done else MUTED, font=(UI_FONT, 11, "bold"))
            for i, n in enumerate(numbers):
                x = 100 + i * step
                got = n in hit
                canvas.create_oval(x - 23, y - 23, x + 23, y + 23, fill=GOLD if got else INSET,
                                   outline=GOLD if got else EDGE, width=2)
                canvas.create_text(x, y, text=str(n), fill="#1a1405" if got else MUTED, font=(UI_FONT, 17, "bold"))
            if done:
                canvas.create_text(100 + 5 * step, y, text="✔", fill=GOLD, font=(UI_FONT, 22, "bold"))

    def refresh_accuracy(self):
        """How often the camera's own reading matched the final (possibly fixed) dice."""
        seen = [t for t in self.throws if t["camera_read"]]
        if not seen:
            model = f"  ({dice_vision.MODEL_STATUS})" if dice_vision else ""
            self.accuracy_var.set("Camera accuracy: no camera throws yet" + model)
            return
        unclear = sum(1 for t in seen if t["camera_read"] == "unclear")
        right = sum(1 for t in seen if t["camera_read"] != "unclear"
                    and sorted(int(v) for v in t["camera_read"].split("-")) == sorted((t["die1"], t["die2"])))
        wrong = len(seen) - unclear - right
        self.accuracy_var.set(f"Camera: {right} of {len(seen)} right ({right / len(seen):.0%}), "
                              f"{wrong} wrong, {unclear} unreadable")

    def refresh_recent(self):
        self.recent_list.delete(0, "end")
        tags = {"camera": "cam", "manual": "typed", "corrected": "fixed"}
        for t in reversed(self.throws[-12:]):
            time_part = t["timestamp"][11:]
            wall = "" if t["back_wall"] else "  (missed wall)"
            tag = tags.get(t["source"], t["source"])
            if t["source"] == "corrected" and t["camera_read"] not in ("", "unclear"):
                tag = f"fixed, cam said {t['camera_read']}"
            elif t["camera_read"] == "unclear":
                tag = "typed, cam couldn't read"
            self.recent_list.insert(
                "end",
                f"{time_part}  {t['dice_set']:<11} {t['position']:<11} {t['die1']}-{t['die2']} = {t['total']:<2} "
                f"[{tag}]{wall}")

    def group_name(self, throw):
        """Which row of the comparison table this throw belongs to."""
        mode = self.compare_var.get()
        if mode == "Position":
            return throw["position"]
        if mode == "Set + Position":
            return f"{throw['dice_set']} @ {throw['position']}"
        return throw["dice_set"]

    def refresh_stats(self):
        self.stats_tree.delete(*self.stats_tree.get_children())
        group_names = []
        for t in self.throws:
            name = self.group_name(t)
            if name not in group_names:
                group_names.append(name)

        runs = bonus_status(self.throws)[5]
        rows = [("All throws", self.throws)] + [
            (name, [t for t in self.throws if self.group_name(t) == name]) for name in group_names
        ]
        for name, subset in rows:
            if not subset:
                continue
            s = summarize(subset)
            rps = f"{s['rolls_per_seven']:.1f}" if s["rolls_per_seven"] else "-"
            low, high = s["range"]
            group_runs = [r for r in runs if name == "All throws" or self.run_group(r) == name]
            numbers = summarize_runs(group_runs)
            self.stats_tree.insert("", "end", values=(
                name,
                s["n"],
                f"{s['seven_rate'] * 100:.1f}%",
                f"{low * 100:.1f}-{high * 100:.1f}%",
                rps,
                f"{s['hardway_rate'] * 100:.1f}%",
                s["verdict"],
                len(group_runs),
                f"{numbers['mean']:.1f}" if group_runs else "-",
                "{}/{}/{}".format(*(sum(1 for r in group_runs if r[k]) for k in ("small", "tall", "all"))),
                numbers["verdict"],
            ))

    def run_group(self, run):
        """A run belongs to whichever group threw most of its rolls."""
        return Counter(self.group_name(t) for t in run["throws"]).most_common(1)[0][0]

    def draw_chart(self):
        c = self.canvas
        c.delete("all")
        chosen_set = self.current_set()
        chosen_position = self.position_var.get().strip()
        mode = self.compare_var.get()
        if mode == "Position":
            chosen = chosen_position
            subset = [t for t in self.throws if t["position"] == chosen_position]
        elif mode == "Set + Position":
            chosen = f"{chosen_set} @ {chosen_position}"
            subset = [t for t in self.throws
                      if t["dice_set"] == chosen_set and t["position"] == chosen_position]
        else:
            chosen = chosen_set
            subset = [t for t in self.throws if t["dice_set"] == chosen_set]
        n = len(subset)
        if n == 0:
            c.create_text(245, 92, text=f"No throws logged yet for '{chosen}'", fill=MUTED)
            return

        observed = {total: 0 for total in range(2, 13)}
        for t in subset:
            observed[t["total"]] += 1

        left, bottom, top = 30, 150, 18
        bar_width, gap = 28, 12
        max_rate = max(max(count / n for count in observed.values()), max(EXPECTED.values())) * 1.15
        scale = (bottom - top) / max_rate

        for i, total in enumerate(range(2, 13)):
            x0 = left + i * (bar_width + gap)
            x1 = x0 + bar_width
            rate = observed[total] / n
            c.create_rectangle(x0, bottom - rate * scale, x1, bottom, fill=GOLD, outline="")
            c.create_text((x0 + x1) / 2, bottom - rate * scale - 8, text=f"{rate * 100:.0f}%", font=(UI_FONT, 8), fill=TEXT)
            expected_y = bottom - EXPECTED[total] * scale
            c.create_line(x0 - 3, expected_y, x1 + 3, expected_y, fill=RED, width=2)
            c.create_text((x0 + x1) / 2, bottom + 14, text=str(total), font=(UI_FONT, 10, "bold"), fill=TEXT)

        c.create_line(left - 5, bottom, left + 11 * (bar_width + gap), bottom, fill=EDGE)
        c.create_text(245, 176, text=f"{chosen}: {n} throws", fill=MUTED)


if __name__ == "__main__":
    root = tk.Tk()
    DiceTracker(root)
    root.mainloop()
