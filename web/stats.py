"""
The tracker's numbers for one player, as dice_tracker_v2.py shows them: the comparison table, the
Small / Tall / All run, the shooter, recent throws, the totals chart and how often the camera was right.
"""

from collections import Counter

from dice_stats import (COMPARE_MODES, DEFAULT_POSITIONS, DEFAULT_SETS, EXPECTED, RANDOM_NUMBERS_PER_RUN,
                        UNSPECIFIED, bonus_status, shooter_status, summarize, summarize_runs)


def group_name(throw, mode):
    if mode == "Position":
        return throw["position"]
    if mode == "Set + Position":
        return f"{throw['dice_set']} @ {throw['position']}"
    return throw["dice_set"]


def stats_rows(throws, mode):
    names = list(dict.fromkeys(group_name(t, mode) for t in throws))
    runs = bonus_status(throws)[5]

    def run_group(run):
        return Counter(group_name(t, mode) for t in run["throws"]).most_common(1)[0][0]

    rows = []
    for name, subset in [("All throws", throws)] + [(n, [t for t in throws if group_name(t, mode) == n]) for n in names]:
        if not subset:
            continue
        s = summarize(subset)
        low, high = s["range"]
        group_runs = [r for r in runs if name == "All throws" or run_group(r) == name]
        numbers = summarize_runs(group_runs)
        rows.append({
            "group": name, "throws": s["n"], "sevens": f"{s['seven_rate'] * 100:.1f}%",
            "range": f"{low * 100:.1f}-{high * 100:.1f}%",
            "rps": f"{s['rolls_per_seven']:.1f}" if s["rolls_per_seven"] else "-",
            "hardways": f"{s['hardway_rate'] * 100:.1f}%", "verdict": s["verdict"], "runs": len(group_runs),
            "numbers": f"{numbers['mean']:.1f}" if group_runs else "-",
            "bonus": "{}/{}/{}".format(*(sum(1 for r in group_runs if r[k]) for k in ("small", "tall", "all"))),
            "numbers_verdict": numbers["verdict"],
        })
    return rows


def accuracy(throws):
    seen = [t for t in throws if t["camera_read"]]
    if not seen:
        return "Camera accuracy: no camera throws yet"
    unclear = sum(1 for t in seen if t["camera_read"] == "unclear")
    right = sum(1 for t in seen if t["camera_read"] != "unclear"
                and sorted(int(v) for v in t["camera_read"].split("-")) == sorted((t["die1"], t["die2"])))
    wrong = len(seen) - unclear - right
    return f"Camera: {right} of {len(seen)} right ({right / len(seen):.0%}), {wrong} wrong, {unclear} unreadable"


def recent(throws):
    tags = {"camera": "cam", "manual": "typed", "corrected": "fixed"}
    out = []
    for t in reversed(throws[-12:]):
        tag = tags.get(t["source"], t["source"])
        if t["source"] == "corrected" and t["camera_read"] not in ("", "unclear"):
            tag = f"fixed, cam said {t['camera_read']}"
        elif t["camera_read"] == "unclear":
            tag = "typed, cam couldn't read"
        out.append({"time": t["timestamp"][11:], "dice_set": t["dice_set"], "position": t["position"],
                    "dice": [t["die1"], t["die2"]], "total": t["total"], "tag": tag, "back_wall": t["back_wall"]})
    return out


def chart(throws, mode, dice_set, position):
    if mode == "Position":
        chosen, subset = position, [t for t in throws if t["position"] == position]
    elif mode == "Set + Position":
        chosen = f"{dice_set} @ {position}"
        subset = [t for t in throws if t["dice_set"] == dice_set and t["position"] == position]
    else:
        chosen, subset = dice_set, [t for t in throws if t["dice_set"] == dice_set]
    counts = Counter(t["total"] for t in subset)
    return {"chosen": chosen, "n": len(subset),
            "bars": [{"total": n, "rate": counts[n] / len(subset) if subset else 0, "expected": EXPECTED[n]}
                     for n in range(2, 13)]}


def tracker_view(throws, mode, dice_set, position):
    mode = mode if mode in COMPARE_MODES else COMPARE_MODES[0]
    hit, run, small_done, tall_done, wins, _ = bonus_status(throws)
    made = Counter(w["kind"] for w in wins)
    point, shooter_rolls, last_run = shooter_status(throws)
    last = throws[-1] if throws else None
    return {
        "mode": mode, "modes": COMPARE_MODES,
        "fair": f"fair dice: 16.7% sevens · 6.0 rolls per seven · {RANDOM_NUMBERS_PER_RUN:.1f} numbers per run",
        "last": {"dice": [last["die1"], last["die2"]], "total": last["total"], "source": last["source"],
                 "dice_set": last["dice_set"], "position": last["position"]} if last else None,
        "shooter": {"point": point, "run": shooter_rolls, "last_run": last_run},
        "bonus": {"hit": sorted(hit), "run": run, "small_done": small_done, "tall_done": tall_done,
                  "made": {k: made[k] for k in ("Small", "Tall", "All")},
                  "wins": [{"time": w["time"][5:16].replace("T", " "), "kind": w["kind"], "rolls": w["rolls"],
                            "sets": ", ".join(f"{name} x{n}" for name, n in w["sets"].most_common())}
                           for w in reversed(wins[-6:])]},
        "rows": stats_rows(throws, mode),
        "chart": chart(throws, mode, dice_set, position),
        "recent": recent(throws),
        "accuracy": accuracy(throws),
    }


def known_names(throws):
    """The dice sets and positions to offer: the usual ones, then any typed in before."""
    sets = list(DEFAULT_SETS)
    for t in throws:
        name = t["dice_set"].split(" ")[0] if t["dice_set"].startswith("Hardway") else t["dice_set"]
        if name not in sets:
            sets.append(name)
    positions = list(DEFAULT_POSITIONS)
    for t in throws:
        if t["position"] not in positions and t["position"] != UNSPECIFIED:
            positions.append(t["position"])
    return sets, positions
