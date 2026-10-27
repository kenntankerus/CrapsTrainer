"""
Craps Table  (sister app to dice_tracker_v2.py)
-----------------------------------------------
Play a full craps table with your own throws. Pick a chip, click a spot on the
layout to bet it, right-click to take a bet down. Click a come bet on its
number (or the odds box under the line) to add odds.

  * Camera on:  throws are read from the webcam, exactly as in the tracker.
                Wrong read?  Type the right two digits and press F2: the roll is
                taken back and settled again with the right dice.
                Not a real throw?  Ctrl+Z takes it back, bets and all.
  * Camera off: type two digits (e.g. 34) and press Enter to roll.

Every throw is also logged to dice_throws.csv (and the dice pictures to dice_crops),
so the tracker's stats and train_dice_model.py see them too. Don't keep the tracker
open at the same time: it rewrites the file from its own copy and would drop these throws.

Run:    python craps_game.py            (or: python craps_game.py 1   to use camera #1)
Needs:  dice_tracker_v2.py, craps_engine.py (and dice_vision.py for the camera) in this folder.
"""

import csv
import json
import math
import os
import queue
import random
import struct
import time
import wave
import tkinter as tk
from datetime import datetime
from tkinter import ttk, messagebox

import dice_tracker_v2 as tracker
from craps_engine import (BONUS_PAYS, HARD_PAYS, HOP_COMBOS, POINTS, SMALL_NUMBERS, TALL_NUMBERS, BetError,
                          CrapsTable, hop_spot, money, spot_name)
from dice_tracker_v2 import (BUTTON, CARD, EDGE, FELT, GOLD, INSET, MUTED, RED, TEXT, UI_FONT, DiceCamera,
                             Speaker, apply_theme, draw_die, parse_dice)

HERE = os.path.dirname(os.path.abspath(__file__))
GAME_FILE = os.path.join(HERE, "craps_game_state.json")      # bankroll, bets and point, so a game can be picked up later
SOUND_DIR = os.path.join(HERE, "sounds")                      # made on first run by make_clack()
DEFAULT_BUY_IN = 10000                                        # cents
BUY_INS = ("$100", "$200", "$500", "$1,000")
FAIR_SEVENS, FAIR_POINTS_MADE, FAIR_HAND = 1 / 6, 0.406, 8.5     # what fair dice give, for the run stats
CHIPS = (100, 500, 2500, 10000, 50000)
CHIP_COLOURS = ((50000, "#6b3fa0", TEXT), (10000, "#1b1b1b", TEXT), (2500, "#2e8b57", TEXT),
                (500, "#c0392b", TEXT), (0, "#ecebe4", "#1a1405"))
LAYOUT_W, LAYOUT_H = 1030, 474         # full-size layout; it shrinks to fit smaller screens
SIDE_WIDTH = 380                        # room kept for the last roll, the rolls list and the camera
LAYOUT_BG = "#0f4a33"
LINE = "#e9e3cf"
WIN, LOSS = GOLD, RED
ODDS_ZONES = {"pass_odds": "pass", "dp_odds": "dont_pass"}
WORDS = {4: "FOUR", 5: "FIVE", 6: "SIX", 8: "EIGHT", 9: "NINE", 10: "TEN"}


def chip_colours(cents):
    return next((fill, text) for floor, fill, text in CHIP_COLOURS if cents >= floor)


def chip_label(cents):
    if cents >= 100000 and cents % 10000 == 0:
        return f"{cents / 100000:g}k"
    return money(cents)[1:]


# ------------------------------------------------------------------ the log
def append_throw(throw):
    is_new = not os.path.exists(tracker.DATA_FILE)
    with open(tracker.DATA_FILE, "a", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=tracker.FIELDS)
        if is_new:
            writer.writeheader()
        writer.writerow(throw)


def replace_throw(throw_id, throw=None):
    """Swap the logged row with this throw_id for `throw`, or drop it when throw is None."""
    with open(tracker.DATA_FILE, newline="") as f:
        rows = list(csv.DictReader(f))
    rows = [throw if row.get("throw_id") == throw_id else row for row in rows]
    rows = [row for row in rows if row is not None]
    with open(tracker.DATA_FILE, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=tracker.FIELDS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def stickman_call(result, variant, voice):
    """The stickman's words for a roll (None = a no-roll), as (text, mood) pieces from the tracker's lines."""
    if result is None:
        keys = ["no_roll"]
    else:
        total, point = result["total"], result["point_before"]
        if point is None:
            keys = [f"co_point_{total}"] if total in POINTS else [f"co_{total}", "coming_out"]
        elif total == 7:
            keys = ["out"] + (["good_roll"] if result["last_run"] >= tracker.HOT_ROLLS[0] else []) + ["new_shooter"]
        elif total == point:
            keys = [f"made_hard_{total}" if result["hard"] and total % 2 == 0 else f"made_{total}", "coming_out"]
        elif total in (4, 6, 8, 10):
            keys = [f"{'hard' if result['hard'] else 'easy'}_{total}"]
        elif total in (5, 9):
            keys = [f"num_{total}"]
        else:
            keys = [f"pt_{total}"]
        if result["previous_total"] == total and keys[0] != "out":
            keys.insert(1, f"b2b_{total}")
        if result["shooter_rolls"] in tracker.HOT_ROLLS:
            keys.append(f"hot_{result['shooter_rolls']}")
        if result["bonus_wins"]:
            keys.insert(1, f"bonus_{result['bonus_wins'][-1]}")
    lines = tracker.voice_lines(voice)
    pieces = []
    for key in keys[:3]:
        variants, mood = lines[key]
        i = variant % len(variants)
        pieces.append((variants[i], mood, f"{key}_{i}"))
    return pieces


def make_clack(path, seed):
    """Write a clay chip landing on another: a sharp knock, then one or two smaller ones as it settles."""
    rng = random.Random(seed)
    rate = 44100
    samples = [0.0] * int(rate * 0.12)
    for start, level in ((0.0, 1.0), (rng.uniform(0.018, 0.03), 0.45), (rng.uniform(0.045, 0.06), 0.18)):
        # a few ringing tones that die away within milliseconds, plus a click of noise at the moment of contact
        modes = [(rng.uniform(2300, 2700), 0.010), (rng.uniform(3600, 4200), 0.006), (rng.uniform(5400, 6300), 0.004)]
        begin = int(start * rate)
        for i in range(begin, min(len(samples), begin + int(rate * 0.06))):
            t = (i - begin) / rate
            value = sum(math.sin(2 * math.pi * freq * t) * math.exp(-t / decay) for freq, decay in modes)
            samples[i] += level * (value + rng.uniform(-1.5, 1.5) * math.exp(-t / 0.0015))
    peak = max(abs(v) for v in samples)
    with wave.open(path, "wb") as f:
        f.setnchannels(1)
        f.setsampwidth(2)
        f.setframerate(rate)
        f.writeframes(b"".join(struct.pack("<h", int(v / peak * 0.7 * 32767)) for v in samples))


def chip_sounds():
    """Paths of the clack variations, made the first time they are needed. Empty if they can't be made or played."""
    if tracker.winsound is None:
        return []
    paths = [os.path.join(SOUND_DIR, f"chip_clack_v1_{i}.wav") for i in range(3)]
    try:
        os.makedirs(SOUND_DIR, exist_ok=True)
        for seed, path in enumerate(paths):
            if not os.path.exists(path):
                make_clack(path, seed)
    except OSError:
        return []
    return paths


def signed(cents):
    return ("+" if cents > 0 else "") + money(cents)


def run_summary(table):
    """The run stats as (section, [(caption, value), ...]) for the stats window."""
    st = table.stats
    rolls, sevens, hands = st["rolls"], st["totals"]["7"], st["hands"]
    started = datetime.fromisoformat(st["started"])
    minutes = int((datetime.now() - started).total_seconds() // 60)
    action = money(st["action"]) + (f"   (result {table.up / st['action']:+.1%} of that)" if st["action"] else "")
    decided = st["points_made"] + hands          # every finished hand ended with a point lost
    money_lines = [
        ("Started", f"{started:%b %d, %H:%M}   ({minutes // 60}h {minutes % 60:02d}m ago)"),
        ("Bought in", money(table.bought_in) + (f"   ({st['buy_ins']} buy-ins)" if st["buy_ins"] > 1 else "")),
        ("Bankroll now", f"{money(table.bankroll + table.on_table)}   ({signed(table.up)})"),
        ("Highest / lowest", f"{signed(st['high'])} / {signed(st['low'])}"),
        ("Total bet (action)", action),
        ("Best / worst roll", f"{signed(st['best_roll'])} / {signed(st['worst_roll'])}"),
    ]
    longest = max(st["longest_hand"], table.shooter_rolls)
    dice_lines = [
        ("Rolls", str(rolls) + (f"   (+ {st['no_rolls']} no-rolls)" if st["no_rolls"] else "")),
        ("Sevens", f"{sevens}   ({sevens / rolls:.1%}, fair dice {FAIR_SEVENS:.1%})" if rolls else "0"),
        ("Hands", f"{hands} finished, {st['hand_rolls'] / hands:.1f} rolls each (fair dice {FAIR_HAND})" if hands
         else "none finished yet"),
        ("Longest / current hand", f"{longest} / {table.shooter_rolls} rolls"),
        ("Points made", f"{st['points_made']} of {decided}" + (
            f"   ({st['points_made'] / decided:.0%}, fair dice {FAIR_POINTS_MADE:.0%})" if decided else "")),
        ("Come-out 7-11 / craps", f"{st['naturals']} / {st['craps']}"),
        ("Small / Tall / All made", "{Small} / {Tall} / {All}".format(**st["bonus"])),
    ]
    return [("MONEY", money_lines), ("DICE", dice_lines)]


def build_zones():
    """Every clickable spot on the layout: key -> (x0, y0, x1, y1). Keys are engine spots, except the odds boxes."""
    zones = {"small": (150, 10, 390, 44), "all": (395, 10, 530, 44), "tall": (535, 10, 775, 44),
             "dont_come": (10, 50, 140, 216)}
    for i, n in enumerate(POINTS):
        x0, x1 = 150 + i * 105, 250 + i * 105
        zones[f"lay_{n}"] = (x0, 50, x1, 72)
        zones[f"dc_{n}"] = (x0, 72, x1, 98)
        zones[f"place_{n}"] = (x0, 98, x1, 168)
        zones[f"come_{n}"] = (x0, 168, x1, 194)
        zones[f"buy_{n}"] = (x0, 194, x1, 216)
    zones.update({
        "come": (150, 222, 775, 280), "field": (150, 286, 775, 346),
        "big6": (10, 222, 73, 346), "big8": (77, 222, 140, 346),
        "dont_pass": (10, 352, 775, 382), "pass": (10, 388, 775, 428),
        "pass_odds": (10, 434, 390, 464), "dp_odds": (395, 434, 775, 464),
        "hard_6": (790, 10, 903, 70), "hard_10": (907, 10, 1020, 70),
        "hard_8": (790, 74, 903, 134), "hard_4": (907, 74, 1020, 134),
        "any7": (790, 140, 1020, 176),
        "aces": (790, 182, 864, 232), "ace_deuce": (868, 182, 942, 232), "twelve": (946, 182, 1020, 232),
        "yo": (790, 236, 864, 286), "horn": (868, 236, 942, 286), "ce": (946, 236, 1020, 286),
        "any_craps": (790, 292, 1020, 328), "hop": (790, 334, 1020, 464),
    })
    return zones


ZONES = build_zones()
LABELS = {"small": "SMALL\n34 to 1", "tall": "TALL\n34 to 1", "all": "MAKE 'EM ALL\n175 to 1",
          "dont_come": "DON'T\nCOME\n\nBar 12", "come": "COME", "big6": "BIG\n6", "big8": "BIG\n8",
          "field": "FIELD    3 · 4 · 9 · 10 · 11      2 pays double · 12 pays triple",
          "dont_pass": "DON'T PASS BAR 12", "pass": "PASS LINE", "pass_odds": "PASS ODDS  (3-4-5x)",
          "dp_odds": "DON'T PASS ODDS  (lay)", "any7": "ANY SEVEN   4 to 1", "aces": "2\n30 to 1",
          "ace_deuce": "3\n15 to 1", "twelve": "12\n30 to 1", "yo": "11\n15 to 1", "horn": "HORN\n2·3·11·12",
          "ce": "C & E\n3 · 7 to 1", "any_craps": "ANY CRAPS   7 to 1"}
LABELS.update({f"hard_{n}": f"HARD {n}\n{pays} to 1" for n, pays in HARD_PAYS.items()})


class CrapsGame:
    def __init__(self, root):
        self.root = root
        apply_theme(root)
        root.title("Craps Table")
        root.state("zoomed")
        self.session = datetime.now().strftime("%Y-%m-%d")
        self.table, saved = self.load_game()
        self.chip = saved.get("chip") if saved.get("chip") in CHIPS else CHIPS[1]
        saved_buy_in = saved.get("buy_in")
        self.buy_in = saved_buy_in if isinstance(saved_buy_in, int) and saved_buy_in > 0 else DEFAULT_BUY_IN
        self.sounds_var = tk.BooleanVar(value=saved.get("sounds", True))
        self.clacks = chip_sounds()
        self.stats_window = None
        self.drag = None                 # a chip being dragged from the rack
        self.hover = None                # the zone it would land on
        self.history = []                # (throw, table snapshot from just before it), for F2 and Ctrl+Z
        self.calls = 0
        self.flash = {}                  # zone -> outline colour for the last roll's winners and losers
        self._flash_job = None
        self.scale = 1.0
        self._fit_job = None
        self.camera = None
        self._last_preview = None
        self.preview_image = None
        self.pending_unclear = None
        self.speaker = Speaker()

        self.build_controls()
        main = ttk.Frame(root, style="Felt.TFrame")
        main.pack(fill="both", expand=True, padx=12, pady=(4, 12))
        self.build_layout(main, saved.get("hop"))
        self.build_side(main)
        self.refresh()

        root.bind("<Configure>", self.schedule_fit)
        root.bind("<Control-z>", lambda e: self.undo())
        root.bind("<F2>", lambda e: self.fix_last())
        root.protocol("WM_DELETE_WINDOW", self.on_close)
        self.dice_entry.focus_set()
        self.speaker.prepare(self.voice_var.get())
        self.poll_camera()

    # ------------------------------------------------------------- building UI
    def build_controls(self):
        self.controls = frame = ttk.Frame(self.root, padding=10)
        frame.pack(fill="x", padx=12, pady=(8, 4))
        state = self.load_state()

        known_sets = list(tracker.DEFAULT_SETS)
        if os.path.exists(tracker.DATA_FILE):
            with open(tracker.DATA_FILE, newline="") as f:
                for row in csv.DictReader(f):
                    name = row["dice_set"].split(" ")[0] if row["dice_set"].startswith("Hardway") else row["dice_set"]
                    if name not in known_sets:
                        known_sets.append(name)
        ttk.Label(frame, text="Dice set:").grid(row=0, column=0, sticky="w")
        self.set_var = tk.StringVar(value=state.get("dice_set") or known_sets[0])
        set_box = ttk.Combobox(frame, textvariable=self.set_var, values=known_sets, width=14)
        set_box.grid(row=0, column=1, padx=5)
        self.setup_var = tk.StringVar(value=state.get("setup", "") if state.get("setup") in tracker.HARDWAY_SETUPS else "")
        self.setup_box = ttk.Combobox(frame, textvariable=self.setup_var, values=[""] + tracker.HARDWAY_SETUPS,
                                      width=5, state="readonly")
        self.set_var.trace_add("write", lambda *_: self.show_setup_box())

        ttk.Label(frame, text="Position:").grid(row=0, column=3, padx=(10, 0), sticky="w")
        self.position_var = tk.StringVar(value=state.get("position") or tracker.DEFAULT_POSITIONS[0])
        ttk.Combobox(frame, textvariable=self.position_var, values=tracker.DEFAULT_POSITIONS, width=11).grid(
            row=0, column=4, padx=5)
        for box in (set_box, self.setup_box):
            box.bind("<<ComboboxSelected>>", lambda e: (self.save_state(), self.dice_entry.focus_set()))

        ttk.Label(frame, text="Dice (e.g. 34):").grid(row=0, column=5, padx=(10, 0))
        self.dice_var = tk.StringVar()
        self.dice_entry = ttk.Entry(frame, textvariable=self.dice_var, width=6, font=(UI_FONT, 14))
        self.dice_entry.grid(row=0, column=6, padx=5)
        self.dice_entry.bind("<Return>", lambda e: self.roll_typed())
        ttk.Button(frame, text="Roll", style="Gold.TButton", command=self.roll_typed).grid(row=0, column=7, padx=2)
        ttk.Button(frame, text="Fix last (F2)", command=self.fix_last).grid(row=0, column=8, padx=2)
        ttk.Button(frame, text="Undo (Ctrl+Z)", command=self.undo).grid(row=0, column=9, padx=2)

        cam = ttk.Frame(frame)
        cam.grid(row=0, column=10, rowspan=2, sticky="nw", padx=(25, 0))
        frame.columnconfigure(10, weight=1)
        self.camera_button = ttk.Button(cam, text="Start camera", style="Gold.TButton", command=self.toggle_camera)
        self.camera_button.grid(row=0, column=0, sticky="w")
        self.camera_status_var = tk.StringVar(value="Camera off")
        ttk.Label(cam, textvariable=self.camera_status_var).grid(row=0, column=1, sticky="w", padx=8)
        self.back_wall_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(cam, text="Hit back wall", variable=self.back_wall_var).grid(row=1, column=0, sticky="w",
                                                                                     pady=(8, 0))
        voice_row = ttk.Frame(cam)
        voice_row.grid(row=1, column=1, sticky="w", padx=8, pady=(8, 0))
        ttk.Label(voice_row, text="Stickman:").pack(side="left")
        voices = ["Off"] + list(tracker.VOICES)
        self.voice_var = tk.StringVar(value=state.get("voice") if state.get("voice") in voices else tracker.DEFAULT_VOICE)
        voice_box = ttk.Combobox(voice_row, textvariable=self.voice_var, values=voices, width=18, state="readonly")
        voice_box.pack(side="left", padx=5)
        voice_box.bind("<<ComboboxSelected>>", lambda e: (self.save_state(), self.speaker.prepare(self.voice_var.get())))

        self.status_var = tk.StringVar(value="Click or drag a chip onto the layout to bet. Right-click a bet, or drag it to the rack, to take it down.")
        ttk.Label(frame, textvariable=self.status_var, style="Muted.TLabel").grid(
            row=1, column=0, columnspan=10, sticky="w", pady=(8, 0))
        self.show_setup_box()

    def build_layout(self, parent, saved_hop):
        left = ttk.Frame(parent, style="Felt.TFrame")
        left.pack(side="left", fill="y")
        self.layout = tk.Canvas(left, width=LAYOUT_W, height=LAYOUT_H, bg=LAYOUT_BG, highlightthickness=0)
        self.layout.pack()
        self.layout.bind("<ButtonPress-1>", self.layout_press)
        self.layout.bind("<B1-Motion>", self.drag_chip)
        self.layout.bind("<ButtonRelease-1>", self.layout_release)
        self.layout.bind("<Button-3>", self.on_right_click)

        self.under = under = ttk.Frame(left, padding=10)
        under.pack(fill="x", pady=(8, 0))
        self.rack = tk.Canvas(under, width=330, height=64, bg=CARD, highlightthickness=2,
                              highlightbackground=CARD)          # turns gold while a bet is dragged over it
        self.rack.grid(row=0, column=0, rowspan=2)
        self.rack.configure(cursor="hand2")
        self.rack.bind("<ButtonPress-1>", self.pick_chip)
        self.rack.bind("<B1-Motion>", self.drag_chip)
        self.rack.bind("<ButtonRelease-1>", self.drop_chip)

        options = ttk.Frame(under)
        options.grid(row=0, column=1, sticky="w", padx=(16, 0))
        ttk.Label(options, text="Hop bet on:").pack(side="left")
        self.hop_var = tk.StringVar(value=saved_hop if saved_hop in HOP_COMBOS else "2-5")
        hop_box = ttk.Combobox(options, textvariable=self.hop_var, values=HOP_COMBOS, width=5, state="readonly")
        hop_box.pack(side="left", padx=5)
        hop_box.bind("<<ComboboxSelected>>", lambda e: self.refresh())
        self.comeout_var = tk.BooleanVar(value=self.table.work_on_comeout)
        ttk.Checkbutton(options, text="Place, buy, lay & hardways work on the come-out", variable=self.comeout_var,
                        command=self.toggle_comeout).pack(side="left", padx=(16, 0))
        ttk.Checkbutton(options, text="Chip sounds", variable=self.sounds_var, command=self.save_game).pack(
            side="left", padx=(16, 0))

        money_row = ttk.Frame(under)
        money_row.grid(row=1, column=1, sticky="w", padx=(16, 0), pady=(8, 0))
        self.money_var = tk.StringVar()
        ttk.Label(money_row, textvariable=self.money_var, font=(UI_FONT, 13, "bold"), foreground=GOLD).pack(side="left")
        buttons = ttk.Frame(under)
        buttons.grid(row=2, column=0, columnspan=2, sticky="w", pady=(8, 0))
        ttk.Button(buttons, text="Take all down", command=self.take_all_down).pack(side="left", padx=(0, 16))
        ttk.Label(buttons, text="Buy-in:").pack(side="left")
        self.buy_in_var = tk.StringVar(value=money(self.buy_in))
        buy_in_box = ttk.Combobox(buttons, textvariable=self.buy_in_var, values=BUY_INS, width=7)
        buy_in_box.pack(side="left", padx=(5, 4))
        buy_in_box.bind("<<ComboboxSelected>>", lambda e: self.read_buy_in())
        buy_in_box.bind("<Return>", lambda e: self.read_buy_in())
        ttk.Button(buttons, text="Rebuy", command=self.rebuy).pack(side="left", padx=4)
        ttk.Button(buttons, text="Reset bankroll", command=self.reset_bankroll).pack(side="left", padx=4)
        ttk.Button(buttons, text="Run stats", style="Gold.TButton", command=self.show_stats).pack(side="left",
                                                                                               padx=(16, 0))

    def build_side(self, parent):
        side = ttk.Frame(parent, style="Felt.TFrame")
        side.pack(side="left", fill="both", expand=True, padx=(12, 0))

        last = ttk.LabelFrame(side, text="LAST ROLL", padding=10)
        last.pack(fill="x")
        row = ttk.Frame(last)
        row.pack(fill="x")
        self.dice_canvas = tk.Canvas(row, width=138, height=64, bg=CARD, highlightthickness=0)
        self.dice_canvas.pack(side="left", padx=(0, 14))
        self.last_var = tk.StringVar(value="No rolls yet")
        ttk.Label(row, textvariable=self.last_var, font=(UI_FONT, 20, "bold"), foreground=GOLD).pack(side="left")
        self.call_var = tk.StringVar()
        ttk.Label(last, textvariable=self.call_var, style="Muted.TLabel", wraplength=SIDE_WIDTH - 20).pack(anchor="w", pady=(6, 0))
        self.payouts = self.make_listbox(last, 6)

        rolls = ttk.LabelFrame(side, text="ROLLS", padding=10)
        rolls.pack(fill="both", expand=True, pady=(10, 0))
        self.roll_list = self.make_listbox(rolls, 4)

        camera_frame = ttk.LabelFrame(side, text="CAMERA", padding=10)
        camera_frame.pack(fill="x", pady=(10, 0))
        self.blank_preview = tk.PhotoImage(width=SIDE_WIDTH - 20, height=(SIDE_WIDTH - 20) * 9 // 16)
        if tracker.dice_vision is not None:
            tracker.dice_vision.PREVIEW_WIDTH = SIDE_WIDTH - 20
        self.preview_label = tk.Label(camera_frame, text="Camera off", image=self.blank_preview, compound="center",
                                      bg=INSET, fg=MUTED, bd=0)
        self.preview_label.pack()
        self.draw_dice(0, 0)

    def make_listbox(self, parent, height):
        box = tk.Listbox(parent, height=height, font=("Consolas", 10), bg=INSET, fg=TEXT, bd=0, highlightthickness=0,
                         selectbackground=BUTTON, selectforeground=TEXT, activestyle="none")
        box.pack(fill="both", expand=True, pady=(8, 0))
        return box

    def show_setup_box(self):
        if self.set_var.get().strip() == "Hardway":
            self.setup_box.grid(row=0, column=2, padx=(0, 5))
        else:
            self.setup_box.grid_remove()

    # ------------------------------------------------------------------ drawing
    def refresh(self):
        self.draw_layout()
        self.draw_rack()
        t = self.table
        self.money_var.set(f"Rack {money(t.bankroll)}   ·   On the table {money(t.on_table)}   ·   "
                           f"{'Up' if t.up >= 0 else 'Down'} {money(abs(t.up))}")
        self.refresh_stats()

    def font(self, size):
        return (UI_FONT, max(6, round(size * self.scale)), "bold")

    def schedule_fit(self, event):
        if event.widget is self.root:
            if self._fit_job:
                self.root.after_cancel(self._fit_job)
            self._fit_job = self.root.after(150, self.fit_layout)

    def fit_layout(self):
        """Shrink the layout on smaller screens so the side panel keeps its room."""
        self._fit_job = None
        room_w = self.root.winfo_width() - SIDE_WIDTH - 40
        room_h = self.root.winfo_height() - self.controls.winfo_reqheight() - self.under.winfo_reqheight() - 40
        scale = max(0.6, min(1.0, room_w / LAYOUT_W, room_h / LAYOUT_H))
        if abs(scale - self.scale) > 0.01:
            self.scale = scale
            self.layout.configure(width=round(LAYOUT_W * scale), height=round(LAYOUT_H * scale))
            self.draw_layout()

    def draw_layout(self):
        """Drawn at full size, then scaled down to fit."""
        c = self.layout
        c.delete("all")
        t = self.table
        for key, (x0, y0, x1, y1) in ZONES.items():
            colour = GOLD if key == self.hover else self.flash.get(key, LINE)
            c.create_rectangle(x0, y0, x1, y1, outline=colour, width=3 if key in self.flash or key == self.hover else 1.5)
            cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
            kind, _, number = key.partition("_")
            if kind == "place":
                c.create_text(cx, cy - 8, text=number, fill=TEXT, font=self.font(26))
                c.create_text(cx, cy + 22, text=WORDS[int(number)], fill=MUTED, font=self.font(8))
            elif kind in ("lay", "buy", "dc", "come") and number:
                text = {"lay": "LAY", "buy": "BUY", "dc": "DON'T COME", "come": "COME"}[kind]
                c.create_text(cx, cy, text=text, fill=MUTED, font=self.font(7))
            elif key == "hop":
                c.create_text(x0 + 10, y0 + 14, anchor="w", text=f"HOP  {self.hop_var.get()}", fill=TEXT, font=self.font(9))
                c.create_text(x0 + 10, y0 + 30, anchor="w", text="easy 15 to 1 · hard 30 to 1", fill=MUTED,
                              font=self.font(8))
            elif key in BONUS_PAYS:
                done = {"small": t.small_done, "tall": t.tall_done, "all": False}[key]
                c.create_text(x0 + 8, cy, anchor="w", text=LABELS[key], fill=GOLD if done else TEXT, font=self.font(8))
                if key != "all":
                    # a marker per number, lit once it has rolled since the last seven
                    numbers = sorted(SMALL_NUMBERS if key == "small" else TALL_NUMBERS)
                    for i, n in enumerate(numbers):
                        mx = x0 + 72 + i * 27
                        hit = n in t.bonus_hits
                        c.create_oval(mx - 12, cy - 12, mx + 12, cy + 12, fill=GOLD if hit else LAYOUT_BG,
                                      outline=GOLD if hit else MUTED, width=1.5)
                        c.create_text(mx, cy, text=str(n), fill="#1a1405" if hit else MUTED, font=self.font(8))
            else:
                big = key in ("pass", "come", "field", "dont_pass")
                if x1 - x0 < 80:
                    cx -= 6         # room for the chip in the corner
                c.create_text(cx, cy, text=LABELS[key], fill=TEXT if big else MUTED, justify="center",
                              font=self.font(13 if key in ("pass", "come") else 9))

        # the puck
        if t.point:
            x0, y0, x1, _ = ZONES[f"place_{t.point}"]
            c.create_oval(x1 - 30, y0 + 4, x1 - 4, y0 + 30, fill="#f4f1e6", outline=GOLD, width=2)
            c.create_text(x1 - 17, y0 + 17, text="ON", fill="#111111", font=self.font(8))
        else:
            x0, y0, x1, _ = ZONES["dont_come"]
            c.create_oval(x1 - 34, y0 + 6, x1 - 6, y0 + 34, fill="#111111", outline=LINE, width=2)
            c.create_text(x1 - 20, y0 + 20, text="OFF", fill="#f4f1e6", font=self.font(8))

        hops = []
        for spot, bet in t.bets.items():
            if spot.startswith("hop_"):
                hops.append(f"{spot_name(spot)[4:]}: {money(bet['amount'])}")
                continue
            x0, y0, x1, y1 = ZONES[spot]
            radius = min(15, (y1 - y0) / 2 - 2)
            cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
            # keep each chip clear of the printed label (and of the puck in the number boxes)
            if spot.startswith(("come_", "dc_")):
                cx = x0 + radius + 20
            elif spot.startswith("place_"):
                cx, cy = x0 + radius + 4, y0 + radius + 4
            elif y1 - y0 > x1 - x0:
                cy = y1 - radius - 8
            elif y1 - y0 <= 60 and x1 - x0 <= 160:
                radius = 9 if x1 - x0 < 80 else 11
                cx, cy = x1 - radius - 3, y0 + radius + 3
            else:
                cx = x1 - radius - 8
            self.draw_chip(cx, cy, radius, bet["amount"], off=not t.working(spot), off_left=cx > (x0 + x1) / 2)
            if bet["odds"]:
                if spot in ("pass", "dont_pass"):
                    ox0, oy0, ox1, oy1 = ZONES["pass_odds" if spot == "pass" else "dp_odds"]
                    self.draw_chip(ox1 - 21, (oy0 + oy1) / 2, 13, bet["odds"])
                else:
                    self.draw_chip(cx + 2 * radius + 6, cy, radius, bet["odds"],
                                   off=spot.startswith("come_") and t.point is None)
        if hops:
            x0, y0, _, _ = ZONES["hop"]
            for i, line in enumerate(hops):
                c.create_text(x0 + 14 + (i // 4) * 110, y0 + 54 + (i % 4) * 18, anchor="w", text=line, fill=GOLD,
                              font=self.font(10))
        c.scale("all", 0, 0, self.scale, self.scale)

    def draw_chip(self, x, y, radius, cents, off=False, off_left=False):
        c = self.layout
        fill, text = chip_colours(cents)
        c.create_oval(x - radius + 2, y - radius + 3, x + radius + 2, y + radius + 3, fill="#05140e", outline="")
        c.create_oval(x - radius, y - radius, x + radius, y + radius, fill=fill, outline=LINE, width=2, dash=(3, 3))
        c.create_text(x, y, text=chip_label(cents), fill=text, font=self.font(8 if radius > 11 else 7))
        if off:
            side = -1 if off_left else 1
            c.create_text(x + side * (radius + 2), y - radius + 2, text="OFF", anchor="e" if off_left else "w", fill=RED,
                          font=self.font(7))

    def draw_rack(self):
        r = self.rack
        r.delete("all")
        for i, cents in enumerate(CHIPS):
            x = 34 + i * 64
            fill, text = chip_colours(cents)
            chosen = cents == self.chip
            y = 28 if chosen else 34
            r.create_oval(x - 25, y - 25, x + 25, y + 25, fill=fill, outline=GOLD if chosen else EDGE,
                          width=4 if chosen else 2)
            r.create_oval(x - 18, y - 18, x + 18, y + 18, outline=LINE, dash=(3, 3))
            r.create_text(x, y, text=chip_label(cents), fill=text, font=(UI_FONT, 11, "bold"))

    def draw_dice(self, die1, die2):
        self.dice_canvas.delete("all")
        draw_die(self.dice_canvas, 2, 2, 56, die1)
        draw_die(self.dice_canvas, 76, 2, 56, die2)

    # ------------------------------------------------------------------ betting
    def zone_at(self, x, y):
        return next((key for key, (x0, y0, x1, y1) in ZONES.items() if x0 <= x <= x1 and y0 <= y <= y1), None)

    def odds_base(self, zone):
        """The bet a click on this zone puts odds on, or None if the zone takes its own bet."""
        if zone in ODDS_ZONES:
            return ODDS_ZONES[zone]
        return zone if zone.startswith(("come_", "dc_")) else None

    def layout_press(self, event):
        """A press on the layout is a click (bet the picked chip) unless it turns into a drag of the bet there."""
        zone = self.zone_at(event.x / self.scale, event.y / self.scale)
        self.drag = {"start": (event.x_root, event.y_root), "window": None, "zone": zone} if zone else None

    def layout_release(self, event):
        drag, self.drag = self.drag, None
        if drag is None:
            return
        if drag["window"] is None:
            self.place_chip(drag["zone"])
            return
        drag["window"].destroy()
        self.rack.configure(highlightbackground=CARD)
        if self.over(self.rack, event.x_root, event.y_root):
            self.take_down_zone(drag["zone"])

    def place_chip(self, zone):
        base = self.odds_base(zone)
        try:
            if base:
                self.table.add_odds(base, self.chip)
                self.status_var.set(f"Odds on {spot_name(base)}: {money(self.table.bets[base]['odds'])}")
            else:
                spot = hop_spot(self.hop_var.get()) if zone == "hop" else zone
                self.table.bet(spot, self.chip)
                self.status_var.set(f"{spot_name(spot)}: {money(self.table.bets[spot]['amount'])}")
            self.clack()
        except BetError as error:
            self.status_var.set(str(error))
            self.root.bell()
        self.after_change()

    def clack(self):
        if self.sounds_var.get() and self.clacks:
            winsound = tracker.winsound
            winsound.PlaySound(random.choice(self.clacks),
                               winsound.SND_FILENAME | winsound.SND_ASYNC | winsound.SND_NODEFAULT)

    def on_right_click(self, event):
        zone = self.zone_at(event.x / self.scale, event.y / self.scale)
        if zone is not None:
            self.take_down_zone(zone)

    def bet_in_zone(self, zone):
        """(spot, is_odds, cents) of what taking down this zone would lift (odds before the bet under them)."""
        base = self.odds_base(zone)
        if base and self.table.bets.get(base, {}).get("odds"):
            return base, True, self.table.bets[base]["odds"]
        spot = base or (hop_spot(self.hop_var.get()) if zone == "hop" else zone)
        bet = self.table.bets.get(spot)
        return (spot, False, bet["amount"] + bet["odds"]) if bet else None

    def take_down_zone(self, zone):
        target = self.bet_in_zone(zone)
        try:
            if target is None:
                raise BetError("No bet there to take down.")
            spot, is_odds, _ = target
            if is_odds:
                back = self.table.take_down_odds(spot)
                self.status_var.set(f"Took down the odds on {spot_name(spot)} ({money(back)})")
            else:
                back = self.table.take_down(spot)
                self.status_var.set(f"Took down {spot_name(spot)} ({money(back)})")
        except BetError as error:
            self.status_var.set(str(error))
            self.root.bell()
        self.after_change()

    def pick_chip(self, event):
        """Pressing a chip in the rack picks it; dragging it onto the layout also bets it there."""
        i = int((event.x - 2) // 64)
        if 0 <= i < len(CHIPS):
            self.chip = CHIPS[i]
            self.after_change()
            self.drag = {"start": (event.x_root, event.y_root), "window": None}

    def drag_chip(self, event):
        """Drags go both ways: a rack chip onto the layout, or a bet on the layout back down to the rack."""
        drag = self.drag
        if drag is None:
            return
        from_table = "zone" in drag
        if drag["window"] is None:
            sx, sy = drag["start"]
            if abs(event.x_root - sx) + abs(event.y_root - sy) < 6:
                return
            target = self.bet_in_zone(drag["zone"]) if from_table else None
            if from_table and target is None:
                self.drag = None            # nothing there to pick up
                return
            drag["window"] = self.floating_chip(target[2] if from_table else self.chip)
        drag["window"].geometry(f"+{event.x_root - 22}+{event.y_root - 22}")
        if from_table:
            over = self.over(self.rack, event.x_root, event.y_root)
            self.rack.configure(highlightbackground=GOLD if over else CARD)
            return
        zone = self.zone_under(event.x_root, event.y_root)
        if zone != self.hover:
            self.hover = zone
            self.draw_layout()

    def drop_chip(self, event):
        drag, self.drag = self.drag, None
        if drag is None or drag["window"] is None:
            return
        drag["window"].destroy()
        zone = self.zone_under(event.x_root, event.y_root)
        self.hover = None
        if zone is None:
            self.draw_layout()
        else:
            self.place_chip(zone)

    def floating_chip(self, cents):
        """A borderless little window with the chip in it, that follows the mouse."""
        window = tk.Toplevel(self.root)
        window.overrideredirect(True)
        window.attributes("-topmost", True)
        clear = "#ff00fe"
        try:
            window.attributes("-transparentcolor", clear)      # Windows: only the round chip shows
        except tk.TclError:
            clear = FELT
        c = tk.Canvas(window, width=44, height=44, bg=clear, highlightthickness=0)
        c.pack()
        fill, text = chip_colours(cents)
        c.create_oval(2, 2, 42, 42, fill=fill, outline=GOLD, width=2)
        c.create_oval(8, 8, 36, 36, outline=LINE, dash=(3, 3))
        c.create_text(22, 22, text=chip_label(cents), fill=text, font=(UI_FONT, 9, "bold"))
        return window

    @staticmethod
    def over(widget, x_root, y_root):
        x, y = x_root - widget.winfo_rootx(), y_root - widget.winfo_rooty()
        return 0 <= x < widget.winfo_width() and 0 <= y < widget.winfo_height()

    def zone_under(self, x_root, y_root):
        if not self.over(self.layout, x_root, y_root):
            return None
        return self.zone_at((x_root - self.layout.winfo_rootx()) / self.scale,
                            (y_root - self.layout.winfo_rooty()) / self.scale)

    def after_change(self):
        self.flash = {}
        self.save_game()
        self.refresh()
        self.dice_entry.focus_set()

    def toggle_comeout(self):
        self.table.work_on_comeout = self.comeout_var.get()
        self.after_change()

    def take_all_down(self):
        back = 0
        for spot in list(self.table.bets):
            for take in (self.table.take_down_odds, self.table.take_down):
                try:
                    back += take(spot)
                except BetError:
                    pass
        self.status_var.set(f"Took down {money(back)}. Contract bets stay up." if back else "Nothing to take down.")
        self.after_change()

    def read_buy_in(self):
        """The buy-in box as cents, or None (with a message) if it isn't a dollar amount."""
        digits = "".join(ch for ch in self.buy_in_var.get().split(".")[0] if ch.isdigit())
        if not digits or int(digits) == 0:
            self.status_var.set("Type a buy-in in whole dollars, like 100.")
            self.root.bell()
            return None
        self.buy_in = int(digits) * 100
        self.buy_in_var.set(money(self.buy_in))
        self.save_game()
        self.dice_entry.focus_set()
        return self.buy_in

    def rebuy(self):
        if self.read_buy_in() is None:
            return
        self.table.rebuy(self.buy_in)
        self.status_var.set(f"Bought in for another {money(self.buy_in)}.")
        self.after_change()

    def reset_bankroll(self):
        if self.read_buy_in() is None:
            return
        if not messagebox.askyesno("Reset bankroll", f"Clear the table and start over with {money(self.buy_in)}?\n\n"
                                   "The run stats start over too. Your logged throws are kept."):
            return
        self.table = CrapsTable(self.buy_in)
        self.table.work_on_comeout = self.comeout_var.get()
        self.history = []
        self.status_var.set(f"Bankroll reset to {money(self.buy_in)}.")
        self.after_change()

    # ---------------------------------------------------------------- run stats
    def show_stats(self):
        if self.stats_window is not None and self.stats_window.winfo_exists():
            self.stats_window.deiconify()
            self.stats_window.lift()
            return
        self.stats_window = window = tk.Toplevel(self.root)
        window.title("Run stats")
        window.configure(bg=FELT)
        self.stats_body = ttk.Frame(window, padding=14)
        self.stats_body.pack(fill="both", expand=True, padx=10, pady=10)
        tree_frame = ttk.LabelFrame(window, text="BY BET", padding=10)
        tree_frame.pack(fill="both", expand=True, padx=10, pady=(0, 10))
        self.stats_tree = ttk.Treeview(tree_frame, columns=("bet", "won", "lost", "net"), show="headings", height=8)
        for col, heading, width in (("bet", "Bet", 130), ("won", "Won", 60), ("lost", "Lost", 60), ("net", "Net", 90)):
            self.stats_tree.heading(col, text=heading)
            self.stats_tree.column(col, width=width, anchor="w" if col == "bet" else "center")
        self.stats_tree.pack(fill="both", expand=True)
        self.refresh_stats()

    def refresh_stats(self):
        if self.stats_window is None or not self.stats_window.winfo_exists():
            return
        for child in self.stats_body.winfo_children():
            child.destroy()
        row = 0
        for section, lines in run_summary(self.table):
            ttk.Label(self.stats_body, text=section, foreground=GOLD, font=(UI_FONT, 9, "bold")).grid(
                row=row, column=0, columnspan=2, sticky="w", pady=(10 if row else 0, 2))
            row += 1
            for caption, value in lines:
                ttk.Label(self.stats_body, text=caption, style="Muted.TLabel").grid(row=row, column=0, sticky="w")
                ttk.Label(self.stats_body, text=value).grid(row=row, column=1, sticky="w", padx=(16, 0))
                row += 1
        self.stats_tree.delete(*self.stats_tree.get_children())
        by_bet = self.table.stats["by_bet"]
        for name in sorted(by_bet, key=lambda n: by_bet[n][2], reverse=True):
            won, lost, net = by_bet[name]
            self.stats_tree.insert("", "end", values=(name, won, lost, signed(net)))

    # ------------------------------------------------------------------ rolling
    def roll_typed(self):
        dice = parse_dice(self.dice_var.get())
        if dice is None:
            self.status_var.set("Type exactly two dice, each 1-6 (for example 34).")
            self.dice_entry.selection_range(0, "end")
            return
        camera_read, crops = "", None
        if self.pending_unclear and time.time() - self.pending_unclear[0] < tracker.UNCLEAR_WINDOW:
            camera_read, crops = "unclear", self.pending_unclear[1]
        self.pending_unclear = None
        self.dice_var.set("")
        self.roll(dice[0], dice[1], "manual", camera_read, crops)

    def roll(self, die1, die2, source, camera_read="", crops=None):
        self.save_state()
        throw_id = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
        if crops and len(crops) == 2:
            self.save_crops(throw_id, crops)
        throw = {"timestamp": datetime.now().isoformat(timespec="seconds"), "session": self.session,
                 "dice_set": self.current_set(), "position": self.position_var.get().strip() or tracker.UNSPECIFIED,
                 "die1": die1, "die2": die2, "total": die1 + die2, "back_wall": self.back_wall_var.get(),
                 "source": source, "camera_read": camera_read, "throw_id": throw_id}
        try:
            append_throw(throw)
        except OSError as error:
            messagebox.showerror("Could not save", str(error))
        self.history.append((throw, self.table.snapshot()))
        self.settle(throw)

    def settle(self, throw):
        result = self.table.roll(throw["die1"], throw["die2"]) if throw["back_wall"] else self.table.no_roll()
        self.show_roll(throw, result)
        self.save_game()
        if self.voice_var.get() in tracker.VOICES:
            self.calls += 1
            self.speaker.say(stickman_call(result, self.calls, self.voice_var.get()), voice=self.voice_var.get())

    def fix_last(self):
        """Take back the last roll and settle it again with the dice typed in the box."""
        if not self.history:
            self.status_var.set("Nothing to fix yet.")
            return
        dice = parse_dice(self.dice_var.get())
        if dice is None:
            self.status_var.set("To fix the last roll, type the two correct dice (like 34) and press F2.")
            self.dice_entry.focus_set()
            return
        throw, snapshot = self.history[-1]
        old = f"{throw['die1']}-{throw['die2']}"
        self.table.restore(snapshot)
        throw.update(die1=dice[0], die2=dice[1], total=sum(dice), source="corrected")
        self.rewrite(throw["throw_id"], throw)
        self.dice_var.set("")
        self.roll_list.delete(0)
        self.settle(throw)
        self.status_var.set(f"Fixed last roll: {old} -> {dice[0]}-{dice[1]}. Bets are back where they were "
                            "before it, then settled again.")
        self.dice_entry.focus_set()

    def undo(self):
        if not self.history:
            return
        throw, snapshot = self.history.pop()
        self.table.restore(snapshot)
        self.rewrite(throw["throw_id"])
        self.delete_crops(throw["throw_id"])
        self.roll_list.delete(0)
        self.flash = {}
        self.payouts.delete(0, "end")
        self.call_var.set("")
        if self.history:
            last = self.history[-1][0]
            self.draw_dice(last["die1"], last["die2"])
            self.last_var.set(f"{last['die1']}-{last['die2']}  =  {last['total']}")
        else:
            self.draw_dice(0, 0)
            self.last_var.set("No rolls yet")
        self.status_var.set(f"Took back {throw['die1']}-{throw['die2']}. Bets are back where they were before it.")
        self.save_game()
        self.refresh()

    def show_roll(self, throw, result):
        self.draw_dice(throw["die1"], throw["die2"])
        self.payouts.delete(0, "end")
        self.flash = {}
        stamp = throw["timestamp"][11:16]
        if result is None:
            self.last_var.set(f"{throw['die1']}-{throw['die2']}  -  NO ROLL")
            self.call_var.set("Missed the back wall. Nothing is settled.")
            self.roll_list.insert(0, f"{stamp} {throw['die1']}-{throw['die2']}      no roll")
            self.refresh()
            return
        total = result["total"]
        self.last_var.set(f"{throw['die1']}-{throw['die2']}  =  {total}")
        if result["seven_out"]:
            state = f"seven out after {result['last_run']} rolls"
        elif result["point_before"] is None and result["point_after"]:
            state = f"point is {result['point_after']}"
        elif result["point_before"] and result["point_after"] is None:
            state = f"point {total} made!"
        else:
            state = f"point {result['point_after']}" if result["point_after"] else "coming out"
        bonus = f" · {', '.join(result['bonus_wins'])} made" if result["bonus_wins"] else ""
        self.call_var.set(state[0].upper() + state[1:] + bonus)

        for spot, net, note in result["events"]:
            self.payouts.insert("end", f"{spot_name(spot):<14}{note:<21}{money(net) if net else '':>9}")
            if spot.startswith("hop_"):
                spot = "hop"
            if spot in ZONES and net:
                self.flash[spot] = WIN if net > 0 else LOSS
        if not result["events"]:
            self.payouts.insert("end", "No action on your bets.")
        net = result["net"]
        self.payouts.insert("end", f"{'This roll':<35}{('+' if net > 0 else '') + money(net):>9}")
        self.roll_list.insert(0, f"{stamp} {throw['die1']}-{throw['die2']} = {total:<2} {state:<24}"
                                 f"{('+' if net > 0 else '') + money(net) if net else '':>8}")
        if self._flash_job:
            self.root.after_cancel(self._flash_job)
        self._flash_job = self.root.after(4000, self.clear_flash)
        self.refresh()

    def clear_flash(self):
        self._flash_job = None
        if self.flash:
            self.flash = {}
            self.draw_layout()

    # ------------------------------------------------------------------- camera
    def toggle_camera(self):
        if self.camera is not None:
            self.stop_camera()
            return
        if DiceCamera is None:
            messagebox.showinfo("Camera not available",
                                "The camera needs OpenCV and the dice_vision.py file next to this one.\n\n"
                                "Install with:  pip install opencv-python numpy\n\n"
                                f"Details: {tracker.CAMERA_IMPORT_ERROR}")
            return
        self.camera_status_var.set("Starting camera...")
        self.root.update_idletasks()
        camera = DiceCamera(tracker.CAMERA_INDEX)
        if not camera.start():
            self.camera_status_var.set("Camera off")
            messagebox.showerror("Camera", camera.error)
            return
        self.camera = camera
        self.camera_button.configure(text="Stop camera")
        self.status_var.set("Camera on. Throw when your bets are down.")

    def stop_camera(self):
        if self.camera is not None:
            self.camera.stop()
            self.camera = None
        self.camera_button.configure(text="Start camera")
        self.camera_status_var.set("Camera off")
        self.preview_image = None
        self._last_preview = None
        self.preview_label.configure(image=self.blank_preview, text="Camera off")

    def beep(self, ok=True):
        try:
            if tracker.winsound is not None:
                tracker.winsound.Beep(1000 if ok else 400, 70)
            else:
                self.root.bell()
        except Exception:
            pass

    def poll_camera(self):
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
                    self.roll(die1, die2, "camera", f"{die1}-{die2}", payload.get("crops"))
                    self.status_var.set(f"Camera read {die1}-{die2}. Wrong? Type the right digits "
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
                self.camera_status_var.set(f"Camera: {camera.status}")
                png = camera.get_preview_png()
                if png is not None and png is not self._last_preview:
                    self._last_preview = png
                    self.preview_image = tk.PhotoImage(data=png)
                    self.preview_label.configure(image=self.preview_image, text="")
        self.root.after(100, self.poll_camera)

    def on_close(self):
        self.save_state()
        self.save_game()
        self.speaker.stop()
        if self.camera is not None:
            self.camera.stop()
            self.camera = None
        self.root.destroy()

    # ------------------------------------------------------------ saving/loading
    def current_set(self):
        name = self.set_var.get().strip() or "Unnamed"
        setup = self.setup_var.get()
        return f"{name} {setup}" if name == "Hardway" and setup else name

    def load_state(self):
        try:
            with open(tracker.STATE_FILE) as f:
                state = json.load(f)
            return state if isinstance(state, dict) else {}
        except (OSError, ValueError):
            return {}

    def save_state(self):
        """The dice set, position and voice are shared with the tracker."""
        state = {"dice_set": self.set_var.get().strip(), "setup": self.setup_var.get(),
                 "position": self.position_var.get().strip(), "voice": self.voice_var.get()}
        try:
            with open(tracker.STATE_FILE, "w") as f:
                json.dump(state, f, indent=2)
        except OSError:
            pass

    def load_game(self):
        """The saved table, and the game's own settings (chip, hop, buy-in, sounds) as a dict."""
        try:
            with open(GAME_FILE) as f:
                saved = json.load(f)
            return CrapsTable.from_dict(saved["table"]), saved
        except (OSError, ValueError, KeyError, TypeError):
            return CrapsTable(DEFAULT_BUY_IN), {}

    def save_game(self):
        try:
            with open(GAME_FILE, "w") as f:
                json.dump({"table": self.table.to_dict(), "chip": self.chip, "hop": self.hop_var.get(),
                           "buy_in": self.buy_in, "sounds": self.sounds_var.get()}, f, indent=2)
        except OSError:
            pass

    def rewrite(self, throw_id, throw=None):
        try:
            replace_throw(throw_id, throw)
        except OSError as error:
            messagebox.showerror("Could not save", str(error))

    def save_crops(self, throw_id, crops):
        try:
            os.makedirs(tracker.CROP_DIR, exist_ok=True)
            for side, png in zip(("left", "right"), crops):
                with open(os.path.join(tracker.CROP_DIR, f"{throw_id}_{side}.png"), "wb") as f:
                    f.write(png)
        except OSError as error:
            self.status_var.set(f"Couldn't save the dice pictures: {error}")

    def delete_crops(self, throw_id):
        for side in ("left", "right"):
            try:
                os.remove(os.path.join(tracker.CROP_DIR, f"{throw_id}_{side}.png"))
            except OSError:
                pass


if __name__ == "__main__":
    root = tk.Tk()
    CrapsGame(root)
    root.mainloop()
