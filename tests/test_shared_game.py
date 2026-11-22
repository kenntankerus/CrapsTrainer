"""
The shared game: seated players all bet on one shooter's dice. Called straight on web.tables (no sign-in),
with an account and its players made in the database.
"""

import itertools
from datetime import datetime

import pytest

from craps_engine import BetError
from web import db, tables

COUNTER = itertools.count()


def table_with(*names):
    """An account with the account holder (the first name) and guests. Returns (user id, [player ids])."""
    db.init()
    now = datetime.now().isoformat(timespec="seconds")
    with db.transaction() as conn:
        user_id = conn.execute("INSERT INTO users (email, name, created) VALUES (?, ?, ?)",
                               (f"shared{next(COUNTER)}@example.com", names[0], now)).lastrowid
        ids = [conn.execute("INSERT INTO players (owner_id, is_self, name, created) VALUES (?, ?, ?, ?)",
                            (user_id, int(i == 0), name, now)).lastrowid for i, name in enumerate(names)]
    return user_id, ids


def bet(user_id, pid, zone, cents=500):
    return tables.change(user_id, pid, "bet", zone=zone, cents=cents)


def roll(user_id, pid, die1, die2):
    return tables.roll(user_id, pid, die1, die2, True, {})[0]


def view(user_id, pid):
    return tables.view(user_id, pid)


def game(user_id, *ids):
    for pid in ids:
        tables.seat(user_id, pid, True)


def test_one_roll_settles_every_seat_and_counts_for_the_shooter():
    user, (kenn, sam, alex) = table_with("Kenn", "Sam", "Alex")
    game(user, kenn, sam, alex)
    assert view(user, kenn)["game"]["shooter"]["id"] == kenn
    bet(user, kenn, "pass")
    bet(user, sam, "dont_pass")
    bet(user, alex, "field")
    said = roll(user, sam, 3, 3)                   # posted for Sam's seat, but Kenn has the dice
    assert said["shared"]
    for pid in (kenn, sam, alex):
        assert view(user, pid)["point"] == 6
    assert view(user, alex)["up"] == -500             # the field lost on 6
    roll(user, kenn, 4, 2)                          # point made
    assert view(user, kenn)["up"] == 500             # the pass line wins (and stays up)
    assert view(user, sam)["up"] == -500
    # the throws are Kenn's alone: the riders are kept out of the tracker and the throw counts
    conn = db.connect()
    assert len(tables.player_throws(conn, kenn)) == 2
    assert tables.player_throws(conn, sam) == [] and tables.player_throws(conn, alex) == []
    assert len(view(user, sam)["rolls"]) == 2       # but Sam sees them on his own Rolls list, with his money


def test_a_new_seat_bets_on_the_games_point():
    user, (kenn, sam) = table_with("Kenn", "Sam")
    game(user, kenn)
    roll(user, kenn, 4, 4)                          # Kenn alone sets the point of 8
    tables.seat(user, sam, True)
    assert view(user, sam)["point"] == 8
    with pytest.raises(BetError):
        bet(user, sam, "pass")                      # no pass line with the point on
    bet(user, sam, "place_6")                       # but a place bet is fine


def test_own_line_bet_on_another_point_keeps_a_player_out():
    user, (kenn, sam) = table_with("Kenn", "Sam")
    bet(user, sam, "pass")
    roll(user, sam, 2, 2)                           # Sam's own game: point 4
    game(user, kenn)
    roll(user, kenn, 5, 5)                          # the shared game: point 10
    with pytest.raises(BetError, match="pass|Pass"):
        tables.seat(user, sam, True)


def test_seven_out_passes_the_dice_and_undo_gives_them_back():
    user, (kenn, sam, alex) = table_with("Kenn", "Sam", "Alex")
    game(user, kenn, sam, alex)
    roll(user, kenn, 4, 4)
    said = roll(user, kenn, 3, 4)
    assert "Sam" in said["notice"]
    assert view(user, kenn)["game"]["shooter"]["id"] == sam
    message, _ = tables.undo(user, alex)            # anyone at the game can take it back
    assert "Everyone" in message
    v = view(user, alex)
    assert v["game"]["shooter"]["id"] == kenn and v["point"] == 8


def test_undo_and_fix_take_back_everyones_bets():
    user, (kenn, sam) = table_with("Kenn", "Sam")
    game(user, kenn, sam)
    bet(user, kenn, "field")
    bet(user, sam, "field", 2500)
    roll(user, kenn, 1, 1)                          # field pays double on 2
    assert view(user, sam)["up"] == 5000
    tables.fix_last(user, sam, 3, 4)                # it was a seven: the field loses for both
    assert view(user, sam)["up"] == -2500 and view(user, kenn)["up"] == -500
    assert view(user, sam)["rolls"][0]["dice"] == [3, 4]
    tables.undo(user, kenn)                         # back to before the roll: both field bets are up again
    assert view(user, sam)["bankroll"] == 10000 - 2500
    assert {b["spot"] for b in view(user, sam)["bets"]} == {"field"}
    assert view(user, kenn)["can_undo"] is False and view(user, sam)["can_undo"] is False


def test_leaving_the_game_and_rolling_alone():
    user, (kenn, sam) = table_with("Kenn", "Sam")
    game(user, kenn, sam)
    roll(user, kenn, 4, 4)
    message = tables.seat(user, kenn, False)
    assert "Sam has the dice" in message
    assert view(user, sam)["game"]["shooter"]["id"] == sam
    roll(user, kenn, 2, 4)                          # Kenn alone again: only his table moves
    assert view(user, sam)["rolls"][0]["dice"] == [4, 4]
    # Sam's last roll was shared, and Kenn has rolled his own since: it can't be changed
    with pytest.raises(BetError, match="shared"):
        tables.undo(user, sam)


def test_reset_keeps_the_games_point():
    user, (kenn, sam) = table_with("Kenn", "Sam")
    game(user, kenn, sam)
    roll(user, kenn, 5, 4)
    tables.change(user, kenn, "reset", cents=20000)
    assert view(user, kenn)["point"] == 9 and view(user, sam)["point"] == 9
    assert view(user, kenn)["bankroll"] == 20000


def test_giving_the_dice_seats_the_player():
    user, (kenn, sam) = table_with("Kenn", "Sam")
    game(user, kenn)
    assert "joined the game" in tables.give_dice(user, sam)
    v = view(user, kenn)["game"]
    assert v["shooter"]["id"] == sam and [s["id"] for s in v["seats"]] == [kenn, sam]
