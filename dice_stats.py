"""
dice_stats.py
-------------
The numbers behind the tracker: seven rates and their 95% ranges, the Small / Tall / All
runs, and the shooter's point and hand length, all worked out from a list of throws.
No window in here, so the web server uses the same code as dice_tracker_v2.py.
"""

import math
import re
from collections import Counter

DEFAULT_SETS = ["3-V", "Hardway", "All Sevens", "Parallel", "Cross-Sixes", "Axis", "Random"]
DEFAULT_POSITIONS = ["12 o'clock", "Stick left", "Stick right"]
UNSPECIFIED = "Unspecified"                  # used for older throws logged before positions existed
COMPARE_MODES = ["Dice set", "Position", "Set + Position"]
# How a Hardway set is held, as "face up - face toward you": 6-2 is sixes up with twos facing the thrower.
HARDWAY_SETUPS = [f"{up}-{facing}" for up in range(1, 7) for facing in range(1, 7) if facing not in (up, 7 - up)]


BONUS_SMALL = (2, 3, 4, 5, 6)
BONUS_TALL = (8, 9, 10, 11, 12)


# Numbers collected per run if the dice are fair: each of the ten comes up before the first 7
# with probability p/(p+p7), and these add up.
_WAYS = {2: 1, 3: 2, 4: 3, 5: 4, 6: 5, 8: 5, 9: 4, 10: 3, 11: 2, 12: 1}
RANDOM_NUMBERS_PER_RUN = sum(w / (w + 6) for w in _WAYS.values())


def summarize_runs(runs):
    """Average numbers collected per run, and whether that is more or less than fair dice give."""
    counts = [r["collected"] for r in runs]
    n = len(counts)
    if n == 0:
        return {"mean": 0.0, "verdict": "no runs yet"}
    mean = sum(counts) / n
    if n < 10:
        return {"mean": mean, "verdict": "too few runs"}
    spread = math.sqrt(sum((c - mean) ** 2 for c in counts) / (n - 1)) / math.sqrt(n)
    if mean > RANDOM_NUMBERS_PER_RUN + 1.96 * spread:
        verdict = "above random"
    elif mean < RANDOM_NUMBERS_PER_RUN - 1.96 * spread:
        verdict = "below random"
    else:
        verdict = "within noise"
    return {"mean": mean, "verdict": verdict}


def bonus_status(throws):
    """Walk the throws in order and track the Small / Tall / All ("Make 'Em All") side bets.
    Each number must come up once before a 7. The run also starts over in a new session, and throws
    that missed the back wall are no-rolls. The tracker doesn't know the point, so any 7 ends a run.
    Returns the current run's state, the wins (with the dice sets thrown in each run) and every run
    that ended on a 7 (how many of the ten numbers it collected, and the throws in it)."""
    hit, run_throws = set(), []
    small_done = tall_done = False
    wins, runs = [], []
    session = None

    def win(kind, t):
        wins.append({"kind": kind, "time": t["timestamp"], "rolls": len(run_throws),
                     "sets": Counter(r["dice_set"] for r in run_throws)})

    for t in throws:
        if t["session"] != session:
            session = t["session"]
            hit, run_throws, small_done, tall_done = set(), [], False, False
        if not t["back_wall"]:
            continue
        run_throws.append(t)
        total = t["total"]
        if total == 7:
            runs.append({"collected": len(hit), "throws": run_throws,
                         "small": small_done, "tall": tall_done, "all": False})
            hit, run_throws, small_done, tall_done = set(), [], False, False
            continue
        hit.add(total)
        if not small_done and hit.issuperset(BONUS_SMALL):
            small_done = True
            win("Small", t)
        if not tall_done and hit.issuperset(BONUS_TALL):
            tall_done = True
            win("Tall", t)
        if small_done and tall_done:
            win("All", t)
            runs.append({"collected": 10, "throws": run_throws, "small": True, "tall": True, "all": True})
            hit, run_throws, small_done, tall_done = set(), [], False, False
    return hit, len(run_throws), small_done, tall_done, wins, runs


def shooter_status(throws):
    """Walk the throws as a craps shooter's turn: come-out, then a point, until a seven-out.
    Returns the point (None on the come-out), rolls in the current run, and rolls in the last finished run.
    Like bonus_status, a new session starts over and no-roll throws are skipped."""
    point, rolls, last = None, 0, None
    session = None
    for t in throws:
        if t["session"] != session:
            session = t["session"]
            point, rolls = None, 0
        if not t["back_wall"]:
            continue
        rolls += 1
        total = t["total"]
        if point is None:
            if total in (4, 5, 6, 8, 9, 10):
                point = total
        elif total == 7:
            point, last, rolls = None, rolls, 0
        elif total == point:
            point = None
    return point, rolls, last


# Chance of each total with two fair dice: 2 -> 1/36, 7 -> 6/36, 12 -> 1/36
EXPECTED = {total: (6 - abs(total - 7)) / 36 for total in range(2, 13)}
RANDOM_SEVEN_RATE = 6 / 36


# ---------------------------------------------------------------- statistics
def wilson_interval(hits, n, z=1.96):
    """95% confidence range for a rate (e.g. the seven rate)."""
    if n == 0:
        return 0.0, 0.0
    p = hits / n
    denom = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return centre - half, centre + half


def summarize(throws):
    """Turn a list of throws into the numbers shown in the table."""
    n = len(throws)
    sevens = sum(1 for t in throws if t["total"] == 7)
    hardways = sum(
        1 for t in throws if t["die1"] == t["die2"] and t["total"] in (4, 6, 8, 10)
    )
    low, high = wilson_interval(sevens, n)

    if n < 30:
        verdict = "too few throws"
    elif low > RANDOM_SEVEN_RATE:
        verdict = "above random"
    elif high < RANDOM_SEVEN_RATE:
        verdict = "below random"
    else:
        verdict = "within noise"

    return {
        "n": n,
        "seven_rate": sevens / n if n else 0,
        "range": (low, high),
        "rolls_per_seven": (n / sevens) if sevens else None,
        "hardway_rate": hardways / n if n else 0,
        "verdict": verdict,
    }


def parse_dice(text):
    """'34' or '3 4' or '3,4' -> (3, 4).  Anything else -> None."""
    if len(re.findall(r"\d", text)) != 2:
        return None
    digits = re.findall(r"[1-6]", text)
    if len(digits) != 2:
        return None
    return int(digits[0]), int(digits[1])
