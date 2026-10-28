"""
Bring the desktop apps' data into an account on the web server.

    python -m web.import_data you@example.com
    python -m web.import_data you@example.com --csv dice_throws.csv --crops dice_crops --game craps_game_state.json

Each user in dice_throws.csv becomes a player at that account's table: the one named by --me (kenn by
default) is the account holder, everyone else a guest, who can later be sent their history. The dice
pictures are copied to the data folder, and craps_game_state.json's bankrolls and bets come along if it's
given. Running it again only adds throws that aren't there yet. Imported throws are kept for the stats,
but Undo and Fix last don't reach back into them.
"""

import argparse
import csv
import json
import os
import shutil
import sys
from datetime import datetime

from . import config, db
from .auth import clean_email, self_player

sys.path.insert(0, config.ROOT)

from dice_stats import UNSPECIFIED  # noqa: E402

FIRST_USER = "kenn"          # dice_tracker_v2.py's name for throws logged before there were users


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("email", help="the account to import into (made if it doesn't exist yet)")
    parser.add_argument("--csv", default=os.path.join(config.ROOT, "dice_throws.csv"))
    parser.add_argument("--crops", default=os.path.join(config.ROOT, "dice_crops"))
    parser.add_argument("--game", default=os.path.join(config.ROOT, "craps_game_state.json"),
                        help="the craps game's saved tables (skipped if the file isn't there)")
    parser.add_argument("--me", default=FIRST_USER, help="which user in the files is the account holder")
    args = parser.parse_args(argv)

    db.init()
    email = clean_email(args.email)
    with open(args.csv, newline="") as f:
        rows = list(csv.DictReader(f))
    stamp = datetime.now().isoformat(timespec="seconds")
    added, players = 0, {}
    with db.transaction() as conn:
        user = conn.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()
        if user is None:
            user_id = conn.execute("INSERT INTO users (email, name, created) VALUES (?, ?, ?)",
                                   (email, args.me.capitalize(), stamp)).lastrowid
        else:
            user_id = user["id"]
        own = self_player(conn, user_id)

        def player_for(name):
            if name not in players:
                if name == args.me:
                    players[name] = own
                else:
                    row = conn.execute("SELECT id FROM players WHERE owner_id = ? AND is_self = 0 AND name = ?",
                                       (user_id, name)).fetchone()
                    players[name] = row["id"] if row else conn.execute(
                        "INSERT INTO players (owner_id, is_self, name, created) VALUES (?, 0, ?, ?)",
                        (user_id, name[:30], stamp)).lastrowid
            return players[name]

        for row in rows:
            player_id = player_for(row.get("user") or FIRST_USER)
            key = (row["timestamp"], row.get("throw_id") or "", int(row["die1"]), int(row["die2"]))
            if conn.execute("SELECT 1 FROM throws WHERE player_id = ? AND timestamp = ? AND throw_id = ? AND die1 = ? "
                            "AND die2 = ?", (player_id, *key)).fetchone():
                continue
            conn.execute(
                "INSERT INTO throws (player_id, throw_id, timestamp, session, dice_set, position, die1, die2, total,"
                " back_wall, source, camera_read, locked) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1)",
                (player_id, key[1], row["timestamp"], row["session"], row["dice_set"],
                 row.get("position") or UNSPECIFIED, key[2], key[3], int(row["total"]),
                 int(row["back_wall"] == "True"), row.get("source") or "manual", row.get("camera_read") or ""))
            added += 1

        tables = {}
        if os.path.exists(args.game):
            with open(args.game) as f:
                saved = json.load(f)
            tables = saved.get("tables") or ({FIRST_USER: saved["table"]} if "table" in saved else {})
            for name, table in tables.items():
                conn.execute("UPDATE players SET table_state = ? WHERE id = ?", (json.dumps(table), player_for(name)))

    copied = 0
    if os.path.isdir(args.crops):
        os.makedirs(config.CROP_DIR, exist_ok=True)
        for name in os.listdir(args.crops):
            target = os.path.join(config.CROP_DIR, name)
            if name.endswith(".png") and not os.path.exists(target):
                shutil.copy2(os.path.join(args.crops, name), target)
                copied += 1
    print(f"{email}: {added} throws added for {', '.join(sorted(players)) or 'nobody'}"
          f"{f', {len(tables)} saved tables' if tables else ''}, {copied} dice pictures copied.")


if __name__ == "__main__":
    main()
