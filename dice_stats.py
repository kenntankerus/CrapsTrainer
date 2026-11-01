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

DEFAULT_SETS = ["3-V", "3-A", "2-V", "2-A", "Hardway", "All Sevens", "Straight Sixes", "Parallel Sixes", "Cross-Sixes", "Random"]
DEFAULT_POSITIONS = ["12 o'clock", "Stick left", "Stick right"]
UNSPECIFIED = "Unspecified"                  # used for older throws logged before positions existed
COMPARE_MODES = ["Dice set", "Position", "Set + Position"]

# How a die is held, as "face up - face toward you": 6-2 is six up with the two facing the thrower.
# Thrown end over end it shows up, toward you, down, then away, so a hold for each die fixes the four
# pairs the dice come up as; the two faces left on the sides don't come up.
HOLDS = [f"{up}-{facing}" for up in range(1, 7) for facing in range(1, 7) if facing not in (up, 7 - up)]
# A Hardway set is both dice held alike with 2, 3, 4 and 5 rolling (1 and 6 on the sides): every pair a hardway.
HARDWAY_SETUPS = [h for h in HOLDS if {int(h[0]), int(h[2])} <= {2, 3, 4, 5}]
CUSTOM_RE = re.compile(r"^Custom L([1-6]-[1-6]) R([1-6]-[1-6])$")
# Holds for each named set, left die then right. The same hold with the dice turning the other way is the
# same set (same_set): 3-V's L3-5 R3-6 brings in L3-2 R3-1. Swapping the dice is a different hold, and can be
# a different set: 2-V's L2-1 R2-4 swapped is 2-A's L2-4 R2-1. (The Axis set was dropped: it is All Sevens.)
NAMED_HOLDS = {
    "3-V": [("3-5", "3-6")],
    "3-A": [("3-1", "3-5")],
    "2-V": [("2-6", "2-3")],
    "2-A": [("2-4", "2-1")],
    "Straight Sixes": [("6-2", "6-2")],                 # and 6-5 on both
    "Parallel Sixes": [("6-3", "6-3")],                 # and 6-4 on both
    # and 6-5 with 6-4 or 6-3: 8 holds
    "Cross-Sixes": [("6-2", "6-3"), ("6-2", "6-4"), ("6-3", "6-2"), ("6-4", "6-2")],
}


def rolling_faces(hold):
    """The faces a die held this way comes up on as it rolls: up, toward you, down, away."""
    up, facing = int(hold[0]), int(hold[2])
    return [up, facing, 7 - up, 7 - facing]


def rolling_pairs(left, right):
    """The four pairs a hold comes up as, top pair first, in the order the dice roll through them."""
    return list(zip(rolling_faces(left), rolling_faces(right)))


def same_set(hold, other):
    """Whether two holds (left, right) are the same set: the same top pair, then the same pairs whichever way
    the dice turn (6-2 and 6-5 on both dice are both straight sixes). Each die keeps its side: swapped round,
    2-V's pairs are 2-A's."""
    pairs = rolling_pairs(*hold)
    turned = [pairs[0]] + pairs[:0:-1]
    return rolling_pairs(*other) in (pairs, turned)


def hold_label(left, right):
    """How a hold is written after the set's name: "6-2" when both dice are held alike, else "L6-2 R1-5"."""
    return left if left == right else f"L{left} R{right}"


def _holds_of(rule):
    return [(left, right) for left in HOLDS for right in HOLDS if rule(left, right)]


# Every hold that is each set, (left, right), for picking which one was thrown: the face toward the shooter
# is logged with every throw, e.g. "Straight Sixes 6-5". Sets with no holds here are logged by name alone.
SET_HOLDS = {
    "Hardway": [(h, h) for h in HARDWAY_SETUPS],
    **{name: _holds_of(lambda left, right, holds=holds: any(same_set((left, right), hold) for hold in holds))
       for name, holds in NAMED_HOLDS.items()},
    "All Sevens": _holds_of(lambda left, right: all(a + b == 7 for a, b in rolling_pairs(left, right))),
}
HOLD_SUFFIX_RE = re.compile(r"^(.+) ([1-6]-[1-6]|L[1-6]-[1-6] R[1-6]-[1-6])$")


def set_name(dice_set):
    """The name to log a throw under. A Custom hold that is really a named set goes under that name, with
    its hold: "Custom L3-2 R3-2" is "Hardway 3-2", "Custom L6-5 R6-5" is "Straight Sixes 6-5", and
    "Custom L6-2 R1-5" (every pair adds up to 7) is "All Sevens L6-2 R1-5". Anything else comes back as it is."""
    dice_set = (dice_set or "").strip()
    match = CUSTOM_RE.match(dice_set)
    if not match:
        return dice_set
    hold = match.groups()
    for name, holds in SET_HOLDS.items():
        if hold in holds:
            return f"{name} {hold_label(*hold)}"
    return dice_set


def set_family(dice_set):
    """The name to offer in the dice set list: "Hardway 6-2" and "Custom L6-2 R6-1" are picked as
    Hardway and Custom, with the hold chosen beside them."""
    match = HOLD_SUFFIX_RE.match(dice_set)
    return match.group(1) if match else dice_set


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
