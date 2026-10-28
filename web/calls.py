"""
What the stickman says about your own roll, on top of his usual lines: the payouts ("Pay the hard eight,
forty-five dollars"), your name, and what a seven-out cost you. Unlike the fixed lines these can't be recorded
ahead of time, so they're made when they're first needed and kept (web/voice.py). Also: how the crowd reacts.
"""

import re

from stickman import WORDS

ONES = ["", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten", "eleven", "twelve",
        "thirteen", "fourteen", "fifteen", "sixteen", "seventeen", "eighteen", "nineteen"]
TENS = ["", "", "twenty", "thirty", "forty", "fifty", "sixty", "seventy", "eighty", "ninety"]
BIG_WIN = 10000           # cents: a roll that wins this much gets the big cheer


def number_words(n):
    """1234 -> 'one thousand two hundred thirty-four'."""
    if n < 20:
        return ONES[n] or "zero"
    if n < 100:
        return TENS[n // 10] + (f"-{ONES[n % 10]}" if n % 10 else "")
    if n < 1000:
        return f"{ONES[n // 100]} hundred" + (f" {number_words(n % 100)}" if n % 100 else "")
    if n < 1_000_000:
        return f"{number_words(n // 1000)} thousand" + (f" {number_words(n % 1000)}" if n % 1000 else "")
    return f"{number_words(n // 1_000_000)} million" + (f" {number_words(n % 1_000_000)}" if n % 1_000_000 else "")


def money_words(cents):
    """500 -> 'five dollars', 583 -> 'five eighty-three' (as a dealer says it), 1 -> 'one cent'."""
    dollars, rest = divmod(abs(cents), 100)
    if not dollars:
        return f"{number_words(rest)} cent{'s' * (rest != 1)}"
    if not rest:
        return f"{number_words(dollars)} dollar{'s' * (dollars != 1)}"
    if rest < 10:
        return f"{number_words(dollars)} dollars and {number_words(rest)} cent{'s' * (rest != 1)}"
    return f"{number_words(dollars)} {number_words(rest)}"


def bet_words(spot):
    """How a dealer names a bet: 'hard_8' -> 'the hard eight'."""
    kind, _, rest = spot.partition("_")
    fixed = {"pass": "the line", "dont_pass": "the don'ts", "field": "the field", "any7": "any seven",
             "any_craps": "any craps", "horn": "the horn", "ce": "C and E", "aces": "aces", "ace_deuce": "ace-deuce",
             "yo": "the yo", "twelve": "twelve", "small": "the Small", "tall": "the Tall", "all": "Make 'em All",
             "big6": "the big six", "big8": "the big eight", "come": "the come", "dont_come": "the don't come"}
    if spot in fixed:
        return fixed[spot]
    if kind == "hop":
        a, b = (int(v) for v in rest.split("_"))
        return f"the hop, {WORDS.get(a, number_words(a))} {WORDS.get(b, number_words(b))}"
    number = WORDS[int(rest)]
    return {"place": f"the {number}", "buy": f"the buy {number}", "lay": f"the lay {number}",
            "hard": f"the hard {number}", "come": f"the come bet on the {number}",
            "dc": f"the don't come on the {number}"}[kind]


def clean_name(name):
    """Just the letters of a player's name the stickman can say."""
    return re.sub(r"[^\w '\-]", "", name or "").strip()[:30]


def personal_call(result, name, voice=""):
    """(text, mood) for this roll, or None when there's nothing to add. result is CrapsTable.roll's."""
    if result is None:
        return None
    name = clean_name(name)
    to_you = f", {name}" if name else ""
    precious = voice.startswith("Precious")
    wins = [(spot, net) for spot, net, _ in result["events"] if net > 0]
    lost = -sum(net for _, net, _ in result["events"] if net < 0)
    total = sum(net for _, net in wins)

    if "All" in result["bonus_wins"] and any(spot == "all" for spot, _ in wins):
        won = next(net for spot, net in wins if spot == "all")
        return f"{name or 'Shooter'} makes 'em all! {money_words(won)} on the All!", "hype"
    if result["seven_out"]:
        if lost >= 2500:
            return (f"That's {money_words(lost)} off the table{to_you}." if not precious else
                    f"{money_words(lost).capitalize()}, gone, precious. All gone."), "down"
        return None
    if not wins:
        return None
    if total >= BIG_WIN:
        return (f"Big winner{to_you}! {money_words(total)} on the roll!" if not precious else
                f"Shiny, shiny! {money_words(total)} for us, precious!"), "hype"
    if len(wins) == 1:
        spot, net = wins[0]
        return (f"Pay {bet_words(spot)}, {money_words(net)}." if not precious else
                f"Pay {bet_words(spot)}, precious. {money_words(net).capitalize()}."), "cheer"
    return (f"{money_words(total).capitalize()} on the roll{to_you}." if not precious else
            f"{money_words(total).capitalize()} for us, precious!"), "cheer"


def reaction(keys, net=0):
    """How the crowd takes a roll, from the stickman's line keys: 'big_cheer', 'cheer', 'groan', 'aww' or None."""
    if not keys:
        return None
    if net >= BIG_WIN or any(k.startswith(("bonus_", "made_hard_")) for k in keys):
        return "big_cheer"
    first = keys[0]
    if first == "out":
        return "groan"
    if first in ("co_2", "co_3", "co_12"):
        return "aww"
    if first.startswith(("made_", "hard_")) or first in ("co_7", "co_11"):
        return "cheer"
    return None
