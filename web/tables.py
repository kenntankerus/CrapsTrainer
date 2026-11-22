"""
The craps table on the server: what craps_game.py does with clicks on its layout, done for a
request instead. Every change loads the player's CrapsTable, changes it and saves it back
inside one transaction, so a phone and a laptop on the same table can't trip over each other.

Players can also play one game together: the seated players all bet on one shooter's dice. Each keeps
their own CrapsTable (bankroll, bets, stats), and the shooter's table carries the game (the point, the
hand's rolls, Small/Tall/All), which is copied onto the others' before anything is settled. A roll is
logged once as the shooter's throw and once more for each other seat as a "rider" row (with that seat's
table before it, so Undo and Fix last take back everyone's bets together); riders stay out of the stats.
"""

import copy
import json
import re
from datetime import datetime, timedelta, timezone

from craps_engine import (BONUS_PAYS, HOP_COMBOS, POINTS, SPOTS, BetError, CrapsTable, hop_spot, money,
                          run_summary, signed, spot_name)
from dice_stats import UNSPECIFIED
from stickman import DEFAULT_VOICE, VOICES, croupier_call, pack_name, stickman_call

from . import db

ODDS_ZONES = {"pass_odds": "pass", "dp_odds": "dont_pass"}
ZONES = (set(SPOTS) - {hop_spot(c) for c in HOP_COMBOS}) | set(ODDS_ZONES) | {"hop"} | {
    f"{kind}_{n}" for kind in ("come", "dc") for n in POINTS}
DEFAULT_BUY_IN = 10000                                  # cents
CHIPS = (100, 500, 2500, 10000, 50000)                  # the rack, as in craps_game.py
LOCAL_TIME = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}$")
# Virtual rolls are dice the server throws for playing with bets. They settle the table and can be fixed or
# taken back like any roll, but they aren't anyone's throws, so the tracker, the stats and the export skip them.
VIRTUAL = "virtual"
# A seat betting on someone else's roll: its bets are settled, but the throw is the shooter's, not theirs
RIDER = "rider"
REAL = f"source NOT IN ('{VIRTUAL}', '{RIDER}')"
# what belongs to the game rather than to a player's bets: the same for everyone at a shared game
GAME_FIELDS = ("point", "shooter_rolls", "last_run", "last_total", "bonus_hits", "bonus_rolls", "small_done",
               "tall_done")
LINE_BETS = ("pass", "dont_pass")


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
    """The player's real throws (virtual rolls left out), oldest first."""
    rows = conn.execute(f"SELECT * FROM throws WHERE player_id = ? AND {REAL} ORDER BY timestamp, id",
                        (player_id,)).fetchall()
    return [throw_dict(r) for r in rows]


# ------------------------------------------------------------------ the shared game
def seated_rows(conn, user_id):
    """The players in the account's shared game, in the order the dice go round."""
    return conn.execute("SELECT * FROM players WHERE owner_id = ? AND seated = 1 ORDER BY is_self DESC, id",
                        (user_id,)).fetchall()


def shooter_of(seats):
    """The seat holding the dice (the first seat, if the shooter has gone)."""
    return next((r for r in seats if r["shooting"]), seats[0] if seats else None)


def copy_game(table, source):
    for field in GAME_FIELDS:
        setattr(table, field, copy.deepcopy(getattr(source, field)))


def sync_game(conn, row, table, buy_in=DEFAULT_BUY_IN):
    """Put the shared game's point and hand on a seated player's table, so their bets go down on the real game."""
    if not row["seated"]:
        return table
    shooter = shooter_of(seated_rows(conn, row["owner_id"]))
    if shooter is not None and shooter["id"] != row["id"]:
        copy_game(table, load_table(shooter, buy_in))
    return table


def load_seat(conn, row, buy_in=DEFAULT_BUY_IN):
    return sync_game(conn, row, load_table(row, buy_in), buy_in)


def set_shooting(conn, user_id, player_id):
    conn.execute("UPDATE players SET shooting = (id = ?) WHERE owner_id = ?", (player_id, user_id))


def next_shooter(seats, shooter_id):
    """Who gets the dice after a seven out: the next seat round the table."""
    ids = [r["id"] for r in seats]
    return seats[(ids.index(shooter_id) + 1) % len(seats)] if shooter_id in ids else None


def seat(user_id, player_id, seated, buy_in=DEFAULT_BUY_IN):
    """Sit a player in on the shared game, or take them out of it. Returns the status line."""
    with db.transaction() as conn:
        row = player_row(conn, user_id, player_id)
        if not seated:
            conn.execute("UPDATE players SET seated = 0, shooting = 0 WHERE id = ?", (player_id,))
            rest = seated_rows(conn, user_id)
            if row["shooting"] and rest:
                set_shooting(conn, user_id, rest[0]["id"])
                return f"{row['name']} left the game. {rest[0]['name']} has the dice."
            return f"{row['name']} left the game. Their bets stay on their own table."
        if row["seated"]:
            return f"{row['name']} is already in the game."
        seats = seated_rows(conn, user_id)
        if seats:
            game = load_table(shooter_of(seats), buy_in)
            table = load_table(row, buy_in)
            line = [s for s in LINE_BETS if s in table.bets]
            if line and table.point != game.point:
                raise BetError(f"{row['name']} has a {spot_name(line[0])} bet on their own "
                               f"{'point of ' + str(table.point) if table.point else 'come-out'}. "
                               "Finish that hand (or reset the bankroll) before joining the game.")
            copy_game(table, game)
            save_table(conn, player_id, table)
            conn.execute("UPDATE players SET seated = 1, shooting = 0 WHERE id = ?", (player_id,))
            return f"{row['name']} joined the game. {shooter_of(seats)['name']} has the dice."
        conn.execute("UPDATE players SET seated = 1 WHERE id = ?", (player_id,))
        set_shooting(conn, user_id, player_id)
        return f"{row['name']} is in the game and has the dice. Add the others to bet on their rolls."


def give_dice(user_id, player_id, buy_in=DEFAULT_BUY_IN):
    """Hand the dice to a player, seating them first if they aren't in the game."""
    joined = not player_row(db.connect(), user_id, player_id)["seated"]
    if joined:
        seat(user_id, player_id, True, buy_in)           # refused (BetError) if their own hand is in the way
    with db.transaction() as conn:
        row = player_row(conn, user_id, player_id)
        set_shooting(conn, user_id, player_id)
    return f"{row['name']} {'joined the game and ' if joined else ''}has the dice."


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
    """The stickman's words for the browser: where to fetch each recording, and the text to speak without it."""
    from .voice import clip_url, spoken
    return [{"clip": name, "url": clip_url(voice, name), "text": text, "spoken": spoken(text, voice), "mood": mood}
            for text, mood, name in pieces]


def speech(pieces, voice, result=None, name="", premium=False, personal=True):
    """What the page plays for a roll: the stickman's call (with a personal line about the roll when there is one)
    and the crowd's reaction. pieces are stickman_call's or croupier_call's, made even with the voice off."""
    from .calls import personal_call, reaction
    from .voice import personal_engine, remember_line, spoken
    net = result["net"] if result else 0
    out = {"call": [], "reaction": reaction([piece[2].rsplit("_", 1)[0] for piece in pieces], net)}
    if voice not in VOICES:
        return out
    out["call"] = call_pieces(pieces, voice)
    line = personal_call(result, name, voice) if personal else None
    engine = personal_engine(voice, premium) if line else None
    if line and engine:
        text, mood = line
        token = remember_line(voice, text, mood, engine)
        out["call"].append({"clip": f"line_{token}", "url": f"/api/voice/{pack_name(voice)}/line/{token}", "text": text,
                            "spoken": spoken(text, voice), "mood": mood, "dynamic": True})
    return out


def new_throw(conn, player_id, die1, die2, back_wall, meta, source="manual", camera_read=""):
    """Build a throw from the request's dice and the thrower's settings (not yet saved)."""
    local = meta.get("local_time") or ""
    if LOCAL_TIME.match(local):
        stamp = local
    elif isinstance(meta.get("tz_offset"), int) and abs(meta["tz_offset"]) <= 14 * 60:
        # a camera throw: the thrower's clock, from the offset their page gave when the camera started
        now = datetime.now(timezone.utc) + timedelta(minutes=meta["tz_offset"])
        stamp = now.replace(tzinfo=None).isoformat(timespec="seconds")
    else:
        stamp = datetime.now().isoformat(timespec="seconds")
    return {"timestamp": stamp, "session": stamp[:10], "dice_set": meta.get("dice_set") or "Unnamed",
            "position": meta.get("position") or UNSPECIFIED, "die1": die1, "die2": die2, "total": die1 + die2,
            "back_wall": bool(back_wall), "source": source, "camera_read": camera_read,
            "throw_id": datetime.now().strftime("%Y%m%d-%H%M%S-%f")}


def insert_throw(conn, player_id, throw, table_before=None, net=None, note="", shot=None):
    return conn.execute(
        "INSERT INTO throws (player_id, throw_id, timestamp, session, dice_set, position, die1, die2, total, back_wall,"
        " source, camera_read, table_before, net, note, shot) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (player_id, throw["throw_id"], throw["timestamp"], throw["session"], throw["dice_set"], throw["position"],
         throw["die1"], throw["die2"], throw["total"], int(throw["back_wall"]), throw["source"], throw["camera_read"],
         json.dumps(table_before) if table_before is not None else None, net, note, shot)).lastrowid


def settle(conn, player_id, table, throw, voice):
    """Roll the table for this throw; returns (note, net, last-roll panel, stickman pieces, the engine's result)."""
    result = table.roll(throw["die1"], throw["die2"]) if throw["back_wall"] else table.no_roll()
    note, panel = describe(result, throw)
    count = conn.execute(f"SELECT COUNT(*) FROM throws WHERE player_id = ? AND source != '{RIDER}'",
                         (player_id,)).fetchone()[0]
    pieces = stickman_call(result, count, voice if voice in VOICES else DEFAULT_VOICE)
    return note, (result["net"] if result else 0), panel, pieces, result


def settle_rider(conn, row, table, throw):
    """Settle another seat's bets on the shooter's throw, logged as a rider row; returns the engine's result."""
    before = table.snapshot()
    result = table.roll(throw["die1"], throw["die2"]) if throw["back_wall"] else table.no_roll()
    note, panel = describe(result, throw)
    ride = {**throw, "throw_id": f"{throw['throw_id']}-{row['id']}", "source": RIDER, "camera_read": ""}
    insert_throw(conn, row["id"], ride, before, result["net"] if result else 0, note, shot=throw["throw_id"])
    save_table(conn, row["id"], table, panel)
    return result


def pass_dice(conn, user_id, shooter_id, result):
    """After a seven out the dice go to the next seat. Returns what the page says, or ""."""
    if not (result and result["seven_out"]):
        return ""
    seats = seated_rows(conn, user_id)
    after = next_shooter(seats, shooter_id) if len(seats) > 1 else None
    if after is None:
        return ""
    set_shooting(conn, user_id, after["id"])
    return f"Seven out. The dice go to {after['name']}."


def roll(user_id, player_id, die1, die2, back_wall, meta, voice=DEFAULT_VOICE, buy_in=DEFAULT_BUY_IN,
         source="manual", camera_read="", premium=False, personal=True):
    """Settle a roll on the table. A roll for anyone in the shared game is the shooter's, and settles every seat.
    Returns what the page plays (see speech; "notice" says when the dice pass) and the throw's id."""
    with db.transaction() as conn:
        row = player_row(conn, user_id, player_id)
        seats = seated_rows(conn, user_id) if row["seated"] else []
        shooter = shooter_of(seats) or row
        table = load_table(shooter, buy_in)
        riders = [(r, load_table(r, buy_in)) for r in seats if r["id"] != shooter["id"]]
        for _, rider_table in riders:
            copy_game(rider_table, table)            # everyone's bets are on the shooter's point
        before = table.snapshot()
        throw = new_throw(conn, shooter["id"], die1, die2, back_wall, meta, source, camera_read)
        note, net, panel, pieces, result = settle(conn, shooter["id"], table, throw, voice)
        insert_throw(conn, shooter["id"], throw, before, net, note, shot=throw["throw_id"] if riders else None)
        save_table(conn, shooter["id"], table, panel)
        for rider, rider_table in riders:
            settle_rider(conn, rider, rider_table, throw)
        notice = pass_dice(conn, user_id, shooter["id"], result) if riders else ""
        said = speech(pieces, voice, result, shooter["name"], premium, personal)
    if riders:
        said["shared"] = True                       # every seat's page should show it, not just the shooter's
    if notice:
        said["notice"] = notice
    return said, throw["throw_id"]


def log_throw(user_id, player_id, die1, die2, back_wall, meta, voice=DEFAULT_VOICE, source="manual", camera_read=""):
    """A tracker throw: logged with no bets settled. The stickman calls it from the throw log, as the tracker does.
    Returns what the page plays (see speech) and the throw's id."""
    with db.transaction() as conn:
        player_row(conn, user_id, player_id)
        throw = new_throw(conn, player_id, die1, die2, back_wall, meta, source, camera_read)
        insert_throw(conn, player_id, throw)
        throws = player_throws(conn, player_id)
    return speech(croupier_call(throws, voice if voice in VOICES else DEFAULT_VOICE), voice), throw["throw_id"]


def last_throw(conn, player_id):
    return conn.execute("SELECT * FROM throws WHERE player_id = ? AND locked = 0 ORDER BY id DESC LIMIT 1",
                        (player_id,)).fetchone()


def last_shot(conn, user_id, row):
    """The rows the last throw made: (the thrower's row, every row of it). For a seated player that's the game's
    last roll, with each other seat's rider row, so Fix last and Undo change everyone's bets together."""
    if row["seated"]:
        ids = [r["id"] for r in seated_rows(conn, user_id)]
        last = conn.execute(f"SELECT * FROM throws WHERE player_id IN ({','.join('?' * len(ids))}) AND locked = 0 "
                            "ORDER BY id DESC LIMIT 1", ids).fetchone()
    else:
        last = last_throw(conn, row["id"])
    if last is None or not last["shot"]:
        return last, [last] if last else []
    rows = conn.execute("SELECT t.* FROM throws t JOIN players p ON p.id = t.player_id WHERE t.shot = ? "
                        "AND p.owner_id = ? AND t.locked = 0 ORDER BY t.id", (last["shot"], user_id)).fetchall()
    for r in rows:
        if last_throw(conn, r["player_id"])["id"] != r["id"]:
            raise BetError("That roll was shared, and someone has rolled since. It can't be changed now.")
    thrower = next((r for r in rows if r["throw_id"] == r["shot"]), rows[0])
    return thrower, rows


def give_back_dice(conn, user_id, thrower):
    """Fixing or taking back a shared roll puts the dice back in the hand that threw it."""
    if thrower["shot"] and player_row(conn, user_id, thrower["player_id"])["seated"]:
        set_shooting(conn, user_id, thrower["player_id"])


def fix_last(user_id, player_id, die1, die2, voice=DEFAULT_VOICE, premium=False, personal=True):
    """Put the right dice on the last throw. A craps roll is taken back and settled again with them."""
    with db.transaction() as conn:
        row = player_row(conn, user_id, player_id)
        last, shot = last_shot(conn, user_id, row)
        if last is None:
            raise BetError("Nothing to fix yet.")
        shooter = player_row(conn, user_id, last["player_id"])
        old = f"{last['die1']}-{last['die2']}"
        throw = throw_dict(last)
        source = last["source"] if last["source"] == VIRTUAL else "corrected"  # a fixed virtual roll stays out of the stats
        throw.update(die1=die1, die2=die2, total=die1 + die2, source=source)
        notice = ""
        if last["table_before"]:
            table = CrapsTable.from_dict(json.loads(last["table_before"]))
            note, net, panel, pieces, result = settle(conn, last["player_id"], table, throw, voice)
            save_table(conn, last["player_id"], table, panel)
            for r in shot:
                if r["id"] == last["id"]:
                    continue
                rider_table = CrapsTable.from_dict(json.loads(r["table_before"]))
                ridden = rider_table.roll(die1, die2) if throw["back_wall"] else rider_table.no_roll()
                rider_note, rider_panel = describe(ridden, throw)
                conn.execute("UPDATE throws SET die1 = ?, die2 = ?, total = ?, net = ?, note = ? WHERE id = ?",
                             (die1, die2, die1 + die2, ridden["net"] if ridden else 0, rider_note, r["id"]))
                save_table(conn, r["player_id"], rider_table, rider_panel)
            if len(shot) > 1:
                give_back_dice(conn, user_id, last)
                notice = pass_dice(conn, user_id, last["player_id"], result)
            message = (f"Fixed last roll: {old} -> {die1}-{die2}. Bets are back where they were before it, "
                       f"then settled again{' for everyone' if len(shot) > 1 else ''}.")
        else:
            note, net, pieces, result = last["note"], last["net"], None, None
            message = f"Fixed last throw: {old} -> {die1}-{die2}"
        conn.execute("UPDATE throws SET die1 = ?, die2 = ?, total = ?, source = ?, net = ?, note = ? "
                     "WHERE id = ?", (die1, die2, die1 + die2, source, net, note, last["id"]))
        if pieces is None:
            throws = player_throws(conn, last["player_id"])
            pieces = croupier_call(throws, voice if voice in VOICES else DEFAULT_VOICE)
        said = speech(pieces, voice, result, shooter["name"], premium, personal)
    return f"{message} {notice}".strip(), said


def undo(user_id, player_id):
    """Take back the last throw; a craps roll puts the bets back where they were before it (everyone's, for a
    roll of the shared game)."""
    with db.transaction() as conn:
        row = player_row(conn, user_id, player_id)
        last, shot = last_shot(conn, user_id, row)
        if last is None:
            raise BetError("Nothing to take back.")
        for r in shot:
            conn.execute("DELETE FROM throws WHERE id = ?", (r["id"],))
            if r["table_before"]:
                table = CrapsTable.from_dict(json.loads(r["table_before"]))
                prev = last_throw(conn, r["player_id"])
                panel = ({"dice": [prev["die1"], prev["die2"]], "total": prev["total"],
                          "no_roll": not prev["back_wall"], "state": "", "events": [], "net": 0} if prev else None)
                save_table(conn, r["player_id"], table, panel)
        if len(shot) > 1:
            give_back_dice(conn, user_id, last)
        if last["table_before"]:
            whose = "Everyone's bets are" if len(shot) > 1 else "Bets are"
            message = f"Took back {last['die1']}-{last['die2']}. {whose} back where they were before it."
        else:
            message = f"Removed {last['die1']}-{last['die2']} ({last['dice_set']})"
    return message, last["throw_id"]


# ------------------------------------------------------------------ changing bets
def change(user_id, player_id, action, buy_in=DEFAULT_BUY_IN, **args):
    """One change to the bets. Returns the status line; BetError carries the dealer's reason for a refusal."""
    with db.transaction() as conn:
        row = player_row(conn, user_id, player_id)
        table = load_seat(conn, row, buy_in)
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
            work, game = table.work_on_comeout, table
            table = CrapsTable(args["cents"])
            table.work_on_comeout = work
            if row["seated"]:
                copy_game(table, game)         # a new bankroll, but the shared game's point stays where it is
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
    table = load_seat(conn, row, buy_in)
    bets = []
    for spot, bet in table.bets.items():
        bets.append({"spot": spot, "zone": "hop" if spot.startswith("hop_") else spot, "name": spot_name(spot),
                     "amount": bet["amount"], "odds": bet["odds"], "working": table.working(spot),
                     "odds_working": table.odds_working(spot)})
    rolls = conn.execute("SELECT die1, die2, total, back_wall, net, note, timestamp, source, "
                         "table_before IS NOT NULL AS game "
                         "FROM throws WHERE player_id = ? ORDER BY id DESC LIMIT 30", (player_id,)).fetchall()
    by_bet = table.stats["by_bet"]
    return {
        "player": {"id": row["id"], "name": row["name"], "is_self": bool(row["is_self"])},
        "bankroll": table.bankroll, "on_table": table.on_table, "up": table.up, "bought_in": table.bought_in,
        "point": table.point, "work_on_comeout": table.work_on_comeout, "shooter_rolls": table.shooter_rolls,
        "last_run": table.last_run,
        "bonus_hits": sorted(table.bonus_hits), "small_done": table.small_done, "tall_done": table.tall_done,
        "bonus_open": not table.bonus_rolls, "bets": bets,
        "last_roll": json.loads(row["last_roll"]) if row["last_roll"] else None,
        "rolls": [{"dice": [r["die1"], r["die2"]], "total": r["total"], "back_wall": bool(r["back_wall"]),
                   "net": r["net"], "note": r["note"], "time": r["timestamp"][11:16], "game": bool(r["game"]),
                   "virtual": r["source"] == VIRTUAL}
                  for r in rolls],
        "can_undo": last_throw(conn, player_id) is not None,
        "summary": run_summary(table),
        "by_bet": [{"bet": name, "won": by_bet[name][0], "lost": by_bet[name][1], "net": signed(by_bet[name][2])}
                   for name in sorted(by_bet, key=lambda n: by_bet[n][2], reverse=True)],
        "bonus_pays": BONUS_PAYS,
        "game": game_view(conn, user_id, buy_in) if row["seated"] else None,
    }


def game_view(conn, user_id, buy_in=DEFAULT_BUY_IN):
    """The shared game's seats, for the strip on the table: who has the dice, and how each seat is doing."""
    seats = seated_rows(conn, user_id)
    shooter = shooter_of(seats)
    out = []
    for r in seats:
        t = load_table(r, buy_in)
        last = json.loads(r["last_roll"]) if r["last_roll"] else None
        out.append({"id": r["id"], "name": r["name"], "shooting": r["id"] == shooter["id"], "bankroll": t.bankroll,
                    "on_table": t.on_table, "up": t.up, "last_net": last["net"] if last else None})
    return {"shooter": {"id": shooter["id"], "name": shooter["name"]}, "seats": out}
