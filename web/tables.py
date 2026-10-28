"""
The craps table on the server: what craps_game.py does with clicks on its layout, done for a
request instead. Every change loads the player's CrapsTable, changes it and saves it back
inside one transaction, so a phone and a laptop on the same table can't trip over each other.
"""

import json
import re
from datetime import datetime

from craps_engine import (BONUS_PAYS, HOP_COMBOS, POINTS, SPOTS, BetError, CrapsTable, hop_spot, money,
                          run_summary, signed, spot_name)
from dice_stats import UNSPECIFIED
from stickman import DEFAULT_VOICE, VOICES, croupier_call, stickman_call

from . import db

ODDS_ZONES = {"pass_odds": "pass", "dp_odds": "dont_pass"}
ZONES = (set(SPOTS) - {hop_spot(c) for c in HOP_COMBOS}) | set(ODDS_ZONES) | {"hop"} | {
    f"{kind}_{n}" for kind in ("come", "dc") for n in POINTS}
DEFAULT_BUY_IN = 10000                                  # cents
CHIPS = (100, 500, 2500, 10000, 50000)                  # the rack, as in craps_game.py
LOCAL_TIME = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}$")


class TableError(Exception):
    """Something the request asked for that doesn't exist (a player, a spot)."""


# ------------------------------------------------------------------ loading
def player_row(conn, user_id, player_id):
    row = conn.execute("SELECT * FROM players WHERE id = ? AND owner_id = ?", (player_id, user_id)).fetchone()
    if row is None:
        raise TableError("No such player at your table.")
    return row


def load_table(row, buy_in=DEFAULT_BUY_IN):
    if row["table_state"]:
        try:
            return CrapsTable.from_dict(json.loads(row["table_state"]))
        except (TypeError, ValueError, AttributeError, KeyError):
            pass
    return CrapsTable(buy_in)


def save_table(conn, player_id, table, last_roll=...):
    conn.execute("UPDATE players SET table_state = ? WHERE id = ?", (json.dumps(table.to_dict()), player_id))
    if last_roll is not ...:
        conn.execute("UPDATE players SET last_roll = ? WHERE id = ?",
                     (json.dumps(last_roll) if last_roll is not None else None, player_id))


def throw_dict(row):
    """A throws row in the shape dice_tracker_v2.py and dice_stats.py use."""
    return {"timestamp": row["timestamp"], "session": row["session"], "dice_set": row["dice_set"],
            "position": row["position"], "die1": row["die1"], "die2": row["die2"], "total": row["total"],
            "back_wall": bool(row["back_wall"]), "source": row["source"], "camera_read": row["camera_read"],
            "throw_id": row["throw_id"]}


def player_throws(conn, player_id):
    rows = conn.execute("SELECT * FROM throws WHERE player_id = ? ORDER BY timestamp, id", (player_id,)).fetchall()
    return [throw_dict(r) for r in rows]


# ------------------------------------------------------------------ betting
def odds_base(zone):
    """The bet a tap on this zone puts odds on, or None if the zone takes its own bet."""
    if zone in ODDS_ZONES:
        return ODDS_ZONES[zone]
    return zone if zone.startswith(("come_", "dc_")) else None


def check_zone(zone, hop):
    if zone not in ZONES:
        raise TableError("That isn't a spot on the layout.")
    if zone == "hop" and hop not in HOP_COMBOS:
        raise TableError("Pick a hop combination.")


def bet_zone(table, zone, cents, hop):
    """Bet cents on a zone (as odds, if it's an odds zone) and return the status line. Raises BetError."""
    base = odds_base(zone)
    if base:
        table.add_odds(base, cents)
        return f"Odds on {spot_name(base)}: {money(table.bets[base]['odds'])}"
    spot = hop_spot(hop) if zone == "hop" else zone
    table.bet(spot, cents)
    return f"{spot_name(spot)}: {money(table.bets[spot]['amount'])}"


def bet_in_zone(table, zone, hop):
    """(spot, is_odds, cents) of what taking down this zone would lift (odds before the bet under them)."""
    base = odds_base(zone)
    if base and table.bets.get(base, {}).get("odds"):
        return base, True, table.bets[base]["odds"]
    spot = base or (hop_spot(hop) if zone == "hop" else zone)
    bet = table.bets.get(spot)
    return (spot, False, bet["amount"] + bet["odds"]) if bet else None


def take_down_zone(table, zone, hop):
    target = bet_in_zone(table, zone, hop)
    if target is None:
        raise BetError("No bet there to take down.")
    spot, is_odds, _ = target
    if is_odds:
        return f"Took down the odds on {spot_name(spot)} ({money(table.take_down_odds(spot))})"
    return f"Took down {spot_name(spot)} ({money(table.take_down(spot))})"


def slide_bet(table, zone, to, hop):
    """Move the bet in one zone to another, for a chip that went down in the wrong place."""
    target = bet_in_zone(table, zone, hop)
    if target is None:
        raise BetError("No bet there to move.")
    spot, is_odds, cents = target
    if is_odds:
        raise BetError("Odds can't slide to another bet. Take them down instead.")
    before = table.snapshot()
    try:
        table.take_down(spot)
        return f"Moved {money(cents)} from {spot_name(spot)}. {bet_zone(table, to, cents, hop)}"
    except BetError:
        table.restore(before)          # a refused move leaves the bet where it was
        raise


def take_all_down(table):
    back = 0
    for spot in list(table.bets):
        for take in (table.take_down_odds, table.take_down):
            try:
                back += take(spot)
            except BetError:
                pass
    return f"Took down {money(back)}. Contract bets stay up." if back else "Nothing to take down."


# ------------------------------------------------------------------ rolling
def describe(result, throw):
    """The roll's one-line state ("point is 6") and what the last-roll panel shows."""
    if result is None:
        return "no roll", {"dice": [throw["die1"], throw["die2"]], "total": throw["total"], "no_roll": True,
                           "state": "Missed the back wall. Nothing is settled.", "events": [], "net": 0}
    total = result["total"]
    if result["seven_out"]:
        state = f"seven out after {result['last_run']} rolls"
    elif result["point_before"] is None and result["point_after"]:
        state = f"point is {result['point_after']}"
    elif result["point_before"] and result["point_after"] is None:
        state = f"point {total} made!"
    else:
        state = f"point {result['point_after']}" if result["point_after"] else "coming out"
    bonus = f" · {', '.join(result['bonus_wins'])} made" if result["bonus_wins"] else ""
    events = [{"spot": spot, "zone": "hop" if spot.startswith("hop_") else spot, "name": spot_name(spot),
               "note": note, "net": net} for spot, net, note in result["events"]]
    return state, {"dice": list(result["dice"]), "total": total, "no_roll": False,
                   "state": state[0].upper() + state[1:] + bonus, "events": events, "net": result["net"]}


def call_pieces(pieces, voice):
    """The stickman's words for the browser: the recorded clip's name and the text to speak without it."""
    from .voice import spoken
    return [{"clip": name, "text": text, "spoken": spoken(text, voice), "mood": mood} for text, mood, name in pieces]


def new_throw(conn, player_id, die1, die2, back_wall, meta, source="manual", camera_read=""):
    """Build a throw from the request's dice and the thrower's settings (not yet saved)."""
    local = meta.get("local_time") or ""
    stamp = local if LOCAL_TIME.match(local) else datetime.now().isoformat(timespec="seconds")
    return {"timestamp": stamp, "session": stamp[:10], "dice_set": meta.get("dice_set") or "Unnamed",
            "position": meta.get("position") or UNSPECIFIED, "die1": die1, "die2": die2, "total": die1 + die2,
            "back_wall": bool(back_wall), "source": source, "camera_read": camera_read,
            "throw_id": datetime.now().strftime("%Y%m%d-%H%M%S-%f")}


def insert_throw(conn, player_id, throw, table_before=None, net=None, note=""):
    return conn.execute(
        "INSERT INTO throws (player_id, throw_id, timestamp, session, dice_set, position, die1, die2, total, back_wall,"
        " source, camera_read, table_before, net, note) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (player_id, throw["throw_id"], throw["timestamp"], throw["session"], throw["dice_set"], throw["position"],
         throw["die1"], throw["die2"], throw["total"], int(throw["back_wall"]), throw["source"], throw["camera_read"],
         json.dumps(table_before) if table_before is not None else None, net, note)).lastrowid


def settle(conn, player_id, table, throw, voice):
    """Roll the table for this throw; returns (note, net, last-roll panel, stickman pieces)."""
    result = table.roll(throw["die1"], throw["die2"]) if throw["back_wall"] else table.no_roll()
    note, panel = describe(result, throw)
    count = conn.execute("SELECT COUNT(*) FROM throws WHERE player_id = ?", (player_id,)).fetchone()[0]
    pieces = stickman_call(result, count, voice) if voice in VOICES else []
    return note, (result["net"] if result else 0), panel, pieces


def roll(user_id, player_id, die1, die2, back_wall, meta, voice=DEFAULT_VOICE, buy_in=DEFAULT_BUY_IN):
    with db.transaction() as conn:
        row = player_row(conn, user_id, player_id)
        table = load_table(row, buy_in)
        before = table.snapshot()
        throw = new_throw(conn, player_id, die1, die2, back_wall, meta)
        note, net, panel, pieces = settle(conn, player_id, table, throw, voice)
        insert_throw(conn, player_id, throw, before, net, note)
        save_table(conn, player_id, table, panel)
    return call_pieces(pieces, voice)


def log_throw(user_id, player_id, die1, die2, back_wall, meta, voice=DEFAULT_VOICE):
    """A tracker throw: logged with no bets settled. The stickman calls it from the throw log, as the tracker does."""
    with db.transaction() as conn:
        player_row(conn, user_id, player_id)
        throw = new_throw(conn, player_id, die1, die2, back_wall, meta)
        insert_throw(conn, player_id, throw)
        throws = player_throws(conn, player_id)
    return call_pieces(croupier_call(throws, voice) if voice in VOICES else [], voice)


def last_throw(conn, player_id):
    return conn.execute("SELECT * FROM throws WHERE player_id = ? AND locked = 0 ORDER BY id DESC LIMIT 1",
                        (player_id,)).fetchone()


def fix_last(user_id, player_id, die1, die2, voice=DEFAULT_VOICE):
    """Put the right dice on the last throw. A craps roll is taken back and settled again with them."""
    with db.transaction() as conn:
        player_row(conn, user_id, player_id)
        last = last_throw(conn, player_id)
        if last is None:
            raise BetError("Nothing to fix yet.")
        old = f"{last['die1']}-{last['die2']}"
        throw = throw_dict(last)
        throw.update(die1=die1, die2=die2, total=die1 + die2, source="corrected")
        if last["table_before"]:
            table = CrapsTable.from_dict(json.loads(last["table_before"]))
            note, net, panel, pieces = settle(conn, player_id, table, throw, voice)
            save_table(conn, player_id, table, panel)
            message = (f"Fixed last roll: {old} -> {die1}-{die2}. Bets are back where they were before it, "
                       "then settled again.")
        else:
            note, net, pieces = last["note"], last["net"], None
            message = f"Fixed last throw: {old} -> {die1}-{die2}"
        conn.execute("UPDATE throws SET die1 = ?, die2 = ?, total = ?, source = 'corrected', net = ?, note = ? "
                     "WHERE id = ?", (die1, die2, die1 + die2, net, note, last["id"]))
        if pieces is None:
            throws = player_throws(conn, player_id)
            pieces = croupier_call(throws, voice) if voice in VOICES else []
    return message, call_pieces(pieces, voice)


def undo(user_id, player_id):
    """Take back the last throw; a craps roll puts the bets back where they were before it."""
    with db.transaction() as conn:
        player_row(conn, user_id, player_id)
        last = last_throw(conn, player_id)
        if last is None:
            raise BetError("Nothing to take back.")
        conn.execute("DELETE FROM throws WHERE id = ?", (last["id"],))
        if last["table_before"]:
            table = CrapsTable.from_dict(json.loads(last["table_before"]))
            prev = last_throw(conn, player_id)
            panel = ({"dice": [prev["die1"], prev["die2"]], "total": prev["total"], "no_roll": not prev["back_wall"],
                      "state": "", "events": [], "net": 0} if prev else None)
            save_table(conn, player_id, table, panel)
            message = f"Took back {last['die1']}-{last['die2']}. Bets are back where they were before it."
        else:
            message = f"Removed {last['die1']}-{last['die2']} ({last['dice_set']})"
    return message, last["throw_id"]


# ------------------------------------------------------------------ changing bets
def change(user_id, player_id, action, buy_in=DEFAULT_BUY_IN, **args):
    """One change to the bets. Returns the status line; BetError carries the dealer's reason for a refusal."""
    with db.transaction() as conn:
        row = player_row(conn, user_id, player_id)
        table = load_table(row, buy_in)
        hop = args.get("hop", "")
        if action == "bet":
            check_zone(args["zone"], hop)
            if args["cents"] not in CHIPS:
                raise TableError("That isn't a chip in the rack.")
            message = bet_zone(table, args["zone"], args["cents"], hop)
        elif action == "take_down":
            check_zone(args["zone"], hop)
            message = take_down_zone(table, args["zone"], hop)
        elif action == "move":
            check_zone(args["zone"], hop)
            check_zone(args["to"], hop)
            message = slide_bet(table, args["zone"], args["to"], hop)
        elif action == "take_all_down":
            message = take_all_down(table)
        elif action == "comeout":
            table.work_on_comeout = bool(args["on"])
            message = "All bets are on during the come-out." if table.work_on_comeout else \
                "Place, buy, lay and hardway bets are off on the come-out."
        elif action == "rebuy":
            table.rebuy(args["cents"])
            message = f"Bought in for another {money(args['cents'])}."
        elif action == "reset":
            work = table.work_on_comeout
            table = CrapsTable(args["cents"])
            table.work_on_comeout = work
            # Undo can't reach back past a reset: the rolls before it stay logged, without their tables
            conn.execute("UPDATE throws SET table_before = NULL, locked = 1 WHERE player_id = ?", (player_id,))
            save_table(conn, player_id, table, None)
            return f"Bankroll reset to {money(args['cents'])}."
        else:
            raise TableError("Unknown change.")
        save_table(conn, player_id, table)
    return message


# ------------------------------------------------------------------ what the page shows
def view(user_id, player_id, buy_in=DEFAULT_BUY_IN):
    conn = db.connect()
    row = player_row(conn, user_id, player_id)
    table = load_table(row, buy_in)
    bets = []
    for spot, bet in table.bets.items():
        bets.append({"spot": spot, "zone": "hop" if spot.startswith("hop_") else spot, "name": spot_name(spot),
                     "amount": bet["amount"], "odds": bet["odds"], "working": table.working(spot),
                     "odds_working": table.odds_working(spot)})
    rolls = conn.execute("SELECT die1, die2, total, back_wall, net, note, timestamp, table_before IS NOT NULL AS game "
                         "FROM throws WHERE player_id = ? ORDER BY id DESC LIMIT 30", (player_id,)).fetchall()
    by_bet = table.stats["by_bet"]
    return {
        "player": {"id": row["id"], "name": row["name"], "is_self": bool(row["is_self"])},
        "bankroll": table.bankroll, "on_table": table.on_table, "up": table.up, "bought_in": table.bought_in,
        "point": table.point, "work_on_comeout": table.work_on_comeout, "shooter_rolls": table.shooter_rolls,
        "bonus_hits": sorted(table.bonus_hits), "small_done": table.small_done, "tall_done": table.tall_done,
        "bonus_open": not table.bonus_rolls, "bets": bets,
        "last_roll": json.loads(row["last_roll"]) if row["last_roll"] else None,
        "rolls": [{"dice": [r["die1"], r["die2"]], "total": r["total"], "back_wall": bool(r["back_wall"]),
                   "net": r["net"], "note": r["note"], "time": r["timestamp"][11:16], "game": bool(r["game"])}
                  for r in rolls],
        "can_undo": last_throw(conn, player_id) is not None,
        "summary": run_summary(table),
        "by_bet": [{"bet": name, "won": by_bet[name][0], "lost": by_bet[name][1], "net": signed(by_bet[name][2])}
                   for name in sorted(by_bet, key=lambda n: by_bet[n][2], reverse=True)],
        "bonus_pays": BONUS_PAYS,
    }
