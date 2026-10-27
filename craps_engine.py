"""
craps_engine.py
---------------
The rules of the craps table that craps_game.py plays on: where each bet may go,
when it is working, and what every roll pays. No window or camera in here.

Money is kept in cents, so odd payouts (a $5 place six wins $5.83) add up exactly.
"""

import copy
from datetime import datetime

POINTS = (4, 5, 6, 8, 9, 10)
TRUE_ODDS = {4: (2, 1), 5: (3, 2), 6: (6, 5), 8: (6, 5), 9: (3, 2), 10: (2, 1)}     # pays first:second
PLACE_PAYS = {4: (9, 5), 5: (7, 5), 6: (7, 6), 8: (7, 6), 9: (7, 5), 10: (9, 5)}
ODDS_LIMIT = {4: 3, 5: 4, 6: 5, 8: 5, 9: 4, 10: 3}       # 3-4-5x odds behind the pass line and come bets
LAY_ODDS_LIMIT = 6                                      # don't-side odds may win up to 6x the flat bet
COMMISSION = 5                                          # percent, charged when a buy or lay bet wins
FIELD_PAYS = {2: 2, 3: 1, 4: 1, 9: 1, 10: 1, 11: 1, 12: 3}
HARD_PAYS = {4: 7, 6: 9, 8: 9, 10: 7}
SINGLE_PAYS = {"aces": (2, 30), "ace_deuce": (3, 15), "yo": (11, 15), "twelve": (12, 30)}   # spot: (total, pays)
BONUS_PAYS = {"small": 34, "tall": 34, "all": 175}
SMALL_NUMBERS, TALL_NUMBERS = {2, 3, 4, 5, 6}, {8, 9, 10, 11, 12}
HOP_COMBOS = [f"{a}-{b}" for a in range(1, 7) for b in range(a, 7)]

ONE_ROLL = ["field", "any7", "any_craps", "horn", "ce"] + list(SINGLE_PAYS)
SPOTS = set(["pass", "dont_pass", "come", "dont_come", "big6", "big8"] + ONE_ROLL + list(BONUS_PAYS)
            + [f"{kind}_{n}" for kind in ("place", "buy", "lay") for n in POINTS]
            + [f"hard_{n}" for n in HARD_PAYS] + ["hop_" + c.replace("-", "_") for c in HOP_COMBOS])
NAMES = {"pass": "Pass line", "dont_pass": "Don't pass", "come": "Come", "dont_come": "Don't come", "field": "Field",
         "big6": "Big 6", "big8": "Big 8", "any7": "Any seven", "any_craps": "Any craps", "horn": "Horn", "ce": "C & E",
         "aces": "Aces", "ace_deuce": "Ace-deuce", "yo": "Yo", "twelve": "Twelve",
         "small": "Small", "tall": "Tall", "all": "Make 'Em All"}
KIND_NAMES = {"come": "Come", "dc": "Don't come", "place": "Place", "buy": "Buy", "lay": "Lay", "hard": "Hard",
              "hop": "Hop"}


class BetError(Exception):
    """A bet the table won't take, with the reason a dealer would give."""


def money(cents):
    sign = "-" if cents < 0 else ""
    dollars, rest = divmod(abs(cents), 100)
    return f"{sign}${dollars:,}" + (f".{rest:02d}" if rest else "")


def spot_name(spot):
    if spot in NAMES:
        return NAMES[spot]
    kind, _, rest = spot.partition("_")
    return f"{KIND_NAMES[kind]} {rest.replace('_', '-')}"


def hop_spot(combo):
    """'2-5' -> 'hop_2_5'"""
    return "hop_" + combo.replace("-", "_")


def vig(cents):
    """5% commission, rounded down to whole dollars, at least $1."""
    return max(100, cents * COMMISSION // 100 // 100 * 100)


def odds_win(number, cents, lay=False):
    first, second = TRUE_ODDS[number]
    return cents * second // first if lay else cents * first // second


def bet_group(spot):
    """Which line of the run stats a bet's wins and losses go to."""
    kind = spot.partition("_")[0]
    if spot in ("pass", "dont_pass", "field"):
        return NAMES[spot]
    if spot == "dont_come" or kind == "dc":
        return "Don't come"
    if kind in ("come", "place", "buy", "lay", "hop"):
        return KIND_NAMES[kind]
    if spot in BONUS_PAYS:
        return "Small/Tall/All"
    if spot in ("big6", "big8"):
        return "Big 6/8"
    return "Hardways" if kind == "hard" else "One-roll props"


def new_stats():
    return {"started": datetime.now().isoformat(timespec="minutes"), "buy_ins": 1, "rolls": 0, "no_rolls": 0,
            "totals": {str(n): 0 for n in range(2, 13)}, "hands": 0, "hand_rolls": 0, "longest_hand": 0,
            "points_set": 0, "points_made": 0, "naturals": 0, "craps": 0, "action": 0, "best_roll": 0,
            "worst_roll": 0, "high": 0, "low": 0, "bonus": {"Small": 0, "Tall": 0, "All": 0},
            "by_bet": {}}                   # group -> [wins, losses, net cents]


class CrapsTable:
    def __init__(self, bankroll=10000):
        self.bankroll = bankroll                # chips in the rack
        self.bought_in = bankroll
        self.bets = {}                          # spot -> {"amount": cents, "odds": cents}
        self.point = None
        self.work_on_comeout = False            # place, buy, lay and hardway bets are off on the come-out unless this is on
        self.shooter_rolls = 0
        self.last_run = None
        self.last_total = None
        self.bonus_hits = set()
        self.bonus_rolls = 0                    # rolls since the last 7; Small/Tall/All only go down at 0
        self.small_done = self.tall_done = False
        self.stats = new_stats()            # for this bankroll; kept here so undo and fixes take them back too

    # --------------------------------------------------------------- saving
    def to_dict(self):
        state = copy.deepcopy(self.__dict__)
        state["bonus_hits"] = sorted(self.bonus_hits)
        return state

    @classmethod
    def from_dict(cls, state):
        table = cls()
        for key, value in copy.deepcopy(state).items():
            if key in table.__dict__:
                setattr(table, key, value)
        table.bonus_hits = set(table.bonus_hits)
        table.stats = {**new_stats(), **table.stats}
        table.bets = {spot: bet for spot, bet in table.bets.items() if spot in SPOTS or spot.startswith(("come_", "dc_"))}
        return table

    snapshot = to_dict

    def restore(self, state):
        self.__dict__.update(CrapsTable.from_dict(state).__dict__)

    @property
    def on_table(self):
        return sum(b["amount"] + b["odds"] for b in self.bets.values())

    def working(self, spot):
        if spot.startswith(("place_", "buy_", "lay_", "hard_")):
            return self.point is not None or self.work_on_comeout
        return True

    # --------------------------------------------------------------- betting
    def bet(self, spot, cents):
        if spot not in SPOTS:
            raise BetError("That isn't a spot you can bet on.")
        if cents > self.bankroll:
            raise BetError(f"Only {money(self.bankroll)} left in your rack.")
        if spot in ("pass", "dont_pass") and self.point:
            raise BetError("Line bets go down on the come-out. With a point on, take odds or bet Come.")
        if spot in ("come", "dont_come") and not self.point:
            raise BetError("Come bets need a point. On the come-out, bet the Pass or Don't Pass line.")
        if spot in BONUS_PAYS and self.bonus_rolls:
            raise BetError("Small, Tall and All only go down before the first roll after a seven.")
        bet = self.bets.setdefault(spot, {"amount": 0, "odds": 0})
        bet["amount"] += cents
        self.bankroll -= cents

    def odds_limit(self, base):
        bet = self.bets[base]
        number = self.point if base in ("pass", "dont_pass") else int(base.split("_")[1])
        if base in ("pass", "come") or base.startswith("come_"):
            return bet["amount"] * ODDS_LIMIT[number]
        first, second = TRUE_ODDS[number]
        return bet["amount"] * LAY_ODDS_LIMIT * first // second

    def add_odds(self, base, cents):
        if base not in self.bets:
            raise BetError(f"There's no {spot_name(base)} bet to put odds on.")
        if base in ("come", "dont_come"):
            raise BetError("Odds go on once the come bet moves to its number.")
        if base in ("pass", "dont_pass") and not self.point:
            raise BetError("Odds go behind the line once a point is set.")
        if cents > self.bankroll:
            raise BetError(f"Only {money(self.bankroll)} left in your rack.")
        bet = self.bets[base]
        limit = self.odds_limit(base)
        if bet["odds"] + cents > limit:
            raise BetError(f"The most odds on {spot_name(base)} is {money(limit)}.")
        bet["odds"] += cents
        self.bankroll -= cents

    def take_down(self, spot):
        """Return the bet (and its odds) to the rack. Returns how much came back."""
        bet = self.bets.get(spot)
        if bet is None:
            raise BetError(f"No bet on {spot_name(spot)}.")
        if (spot == "pass" and self.point) or spot.startswith("come_"):
            raise BetError(f"{spot_name(spot)} is a contract bet once a point is set. You can take down its odds.")
        if spot in BONUS_PAYS and self.bonus_rolls:
            raise BetError("Small, Tall and All stay up until they win or a seven rolls.")
        del self.bets[spot]
        back = bet["amount"] + bet["odds"]
        self.bankroll += back
        return back

    def take_down_odds(self, base):
        bet = self.bets.get(base)
        if bet is None or not bet["odds"]:
            raise BetError(f"No odds on {spot_name(base)}.")
        back, bet["odds"] = bet["odds"], 0
        self.bankroll += back
        return back

    def rebuy(self, cents):
        self.bankroll += cents
        self.bought_in += cents
        self.stats["buy_ins"] += 1

    @property
    def up(self):
        return self.bankroll + self.on_table - self.bought_in

    def _tally(self, spot, stake, net):
        line = self.stats["by_bet"].setdefault(bet_group(spot), [0, 0, 0])
        line[0 if net > 0 else 1] += 1
        line[2] += net
        self.stats["action"] += stake

    def no_roll(self):
        self.stats["no_rolls"] += 1

    # --------------------------------------------------------------- rolling
    def roll(self, die1, die2):
        """Settle every bet for one roll that hit the back wall.
        Returns the roll's details and events: a list of (spot, net cents, note)."""
        total, hard = die1 + die2, die1 == die2
        point_before = self.point
        comeout = point_before is None
        events = []

        def win(spot, won, stays=True, note="wins"):
            bet = self.bets[spot]
            self.bankroll += won
            self._tally(spot, bet["amount"] + bet["odds"], won)
            if not stays:
                self.bankroll += bet["amount"] + bet["odds"]
                del self.bets[spot]
            events.append((spot, won, note))

        def lose(spot, keep_odds=False):
            bet = self.bets.pop(spot)
            lost = bet["amount"] + (0 if keep_odds else bet["odds"])
            self._tally(spot, lost, -lost)
            if keep_odds:
                self.bankroll += bet["odds"]
            events.append((spot, -lost, "loses"))

        for spot in list(self.bets):
            amount = self.bets[spot]["amount"]
            kind, _, rest = spot.partition("_")

            if spot in ONE_ROLL or kind == "hop":
                won = None
                if spot == "field" and total in FIELD_PAYS:
                    won = amount * FIELD_PAYS[total]
                elif spot == "any7" and total == 7:
                    won = amount * 4
                elif spot == "any_craps" and total in (2, 3, 12):
                    won = amount * 7
                elif spot in SINGLE_PAYS and total == SINGLE_PAYS[spot][0]:
                    won = amount * SINGLE_PAYS[spot][1]
                elif spot == "horn" and total in (2, 3, 11, 12):
                    # four equal bets: the one that hits is paid, the other three lose, and the horn stays up
                    won = amount * (30 if total in (2, 12) else 15) // 4 - amount * 3 // 4
                elif spot == "ce" and total in (2, 3, 11, 12):
                    won = amount * (7 if total == 11 else 3)
                elif kind == "hop" and sorted((die1, die2)) == [int(v) for v in rest.split("_")]:
                    won = amount * (30 if hard else 15)
                if won is None:
                    lose(spot)
                else:
                    win(spot, won)

            elif kind == "hard" and self.working(spot):
                n = int(rest)
                if total == n and hard:
                    win(spot, amount * HARD_PAYS[n])
                elif total in (7, n):
                    lose(spot)

            elif spot in ("big6", "big8"):
                if total == int(spot[3]):
                    win(spot, amount)
                elif total == 7:
                    lose(spot)

            elif kind in ("place", "buy", "lay") and self.working(spot):
                n = int(rest)
                if kind == "lay":
                    if total == 7:
                        won = odds_win(n, amount, lay=True)
                        win(spot, won - vig(won))
                    elif total == n:
                        lose(spot)
                elif total == n:
                    first, second = PLACE_PAYS[n]
                    win(spot, amount * first // second if kind == "place" else odds_win(n, amount) - vig(amount))
                elif total == 7:
                    lose(spot)

            elif kind == "come" and rest:
                # a come bet sitting on its number; its odds are off on the come-out
                n, odds = int(rest), self.bets[spot]["odds"]
                if total == n:
                    if comeout:
                        win(spot, amount, stays=False, note="wins (odds were off)" if odds else "wins")
                    else:
                        win(spot, amount + odds_win(n, odds), stays=False)
                elif total == 7:
                    lose(spot, keep_odds=comeout)

            elif kind == "dc" and rest:
                n = int(rest)
                if total == 7:
                    win(spot, amount + odds_win(n, self.bets[spot]["odds"], lay=True), stays=False)
                elif total == n:
                    lose(spot)

        # New come bets settle or move to their number after the old ones on that number have been paid
        if "come" in self.bets:
            if total in (7, 11):
                win("come", self.bets["come"]["amount"], stays=total == 11)
            elif total in (2, 3, 12):
                lose("come")
            else:
                self.bets[f"come_{total}"] = self.bets.pop("come")
                events.append((f"come_{total}", 0, f"moves to the {total}"))
        if "dont_come" in self.bets:
            if total in (2, 3):
                win("dont_come", self.bets["dont_come"]["amount"])
            elif total == 12:
                events.append(("dont_come", 0, "bar 12, push"))
            elif total in (7, 11):
                lose("dont_come")
            else:
                self.bets[f"dc_{total}"] = self.bets.pop("dont_come")
                events.append((f"dc_{total}", 0, f"moves behind the {total}"))

        # The line: the flat bet stays up after a win, its odds come back
        if "pass" in self.bets:
            bet = self.bets["pass"]
            if comeout:
                if total in (7, 11):
                    win("pass", bet["amount"])
                elif total in (2, 3, 12):
                    lose("pass")
            elif total == point_before:
                win("pass", bet["amount"] + odds_win(total, bet["odds"]))
                self.bankroll += bet["odds"]
                bet["odds"] = 0
            elif total == 7:
                lose("pass")
        if "dont_pass" in self.bets:
            bet = self.bets["dont_pass"]
            if comeout:
                if total in (2, 3):
                    win("dont_pass", bet["amount"])
                elif total == 12:
                    events.append(("dont_pass", 0, "bar 12, push"))
                elif total in (7, 11):
                    lose("dont_pass")
            elif total == 7:
                win("dont_pass", bet["amount"] + odds_win(point_before, bet["odds"], lay=True))
                self.bankroll += bet["odds"]
                bet["odds"] = 0
            elif total == point_before:
                lose("dont_pass")

        seven_out = not comeout and total == 7
        if comeout and total in POINTS:
            self.point = total
        elif not comeout and total in (7, point_before):
            self.point = None
        self.shooter_rolls += 1
        if seven_out:
            self.last_run, self.shooter_rolls = self.shooter_rolls, 0
        previous_total, self.last_total = self.last_total, total

        bonus_wins = self._bonus(total, events)
        net = sum(e[1] for e in events)
        st = self.stats
        st["rolls"] += 1
        st["totals"][str(total)] += 1
        if comeout:
            key = "naturals" if total in (7, 11) else "craps" if total in (2, 3, 12) else "points_set"
            st[key] += 1
        elif total == point_before:
            st["points_made"] += 1
        if seven_out:
            st["hands"] += 1
            st["hand_rolls"] += self.last_run
            st["longest_hand"] = max(st["longest_hand"], self.last_run)
        st["best_roll"], st["worst_roll"] = max(st["best_roll"], net), min(st["worst_roll"], net)
        st["high"], st["low"] = max(st["high"], self.up), min(st["low"], self.up)
        for name in bonus_wins:
            st["bonus"][name] += 1
        return {"dice": (die1, die2), "total": total, "hard": hard, "point_before": point_before,
                "point_after": self.point, "seven_out": seven_out, "shooter_rolls": self.shooter_rolls,
                "last_run": self.last_run, "previous_total": previous_total, "bonus_wins": bonus_wins,
                "events": events, "net": net}

    def _bonus(self, total, events):
        """Small / Tall / All: every number of the group before a seven. Any seven ends the run."""
        self.bonus_rolls += 1
        if total == 7:
            for spot in BONUS_PAYS:
                if spot in self.bets:
                    bet = self.bets.pop(spot)
                    self._tally(spot, bet["amount"], -bet["amount"])
                    events.append((spot, -bet["amount"], "loses"))
            self.bonus_hits, self.bonus_rolls, self.small_done, self.tall_done = set(), 0, False, False
            return []
        self.bonus_hits.add(total)
        made = []
        if not self.small_done and self.bonus_hits >= SMALL_NUMBERS:
            self.small_done = True
            made.append("Small")
        if not self.tall_done and self.bonus_hits >= TALL_NUMBERS:
            self.tall_done = True
            made.append("Tall")
        if self.small_done and self.tall_done:
            made.append("All")
        for name in made:
            spot = name.lower()
            if spot in self.bets:
                bet = self.bets.pop(spot)
                self._tally(spot, bet["amount"], bet["amount"] * BONUS_PAYS[spot])
                self.bankroll += bet["amount"] * (BONUS_PAYS[spot] + 1)
                events.append((spot, bet["amount"] * BONUS_PAYS[spot], "wins"))
        if "All" in made:
            self.bonus_hits, self.bonus_rolls, self.small_done, self.tall_done = set(), 0, False, False
        return made
