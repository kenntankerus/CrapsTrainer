"""
stickman.py
-----------
Everything the stickman can say, in each character's voice, and which lines go with a roll.
Kept apart from the windows so the tracker, the craps game and the web server all call
the dice the same way.
"""

import re

from dice_stats import bonus_status, shooter_status

WORDS = {2: "two", 3: "three", 4: "four", 5: "five", 6: "six", 7: "seven", 8: "eight", 9: "nine", 10: "ten",
         11: "eleven", 12: "twelve"}
POINT_NUMBERS = (4, 5, 6, 8, 9, 10)
HOT_ROLLS = (10, 15, 20, 25, 30, 40, 50)        # the stickman remarks on a long hand at these roll counts


def _build_lines():
    """Every line the stickman can say: key -> (variants, mood). Kept as data so the voice can be made ahead of time."""
    lines = {
        "no_roll": (["No roll! Gotta hit the back wall.", "No roll, no roll. Off the back wall, shooter."], "calm"),
        "co_7": (["Seven! Front line winner! Pay the line.", "Winner seven! Front line winner, take the don'ts."], "hype"),
        "co_11": (["Yo eleven! Front line winner!", "Eleven, yo! Pay the line!"], "hype"),
        "co_2": (["Two, craps. Aces. Line away.", "Aces! Craps two, line away."], "down"),
        "co_3": (["Three, craps. Ace-deuce. Line away.", "Craps three, ace-deuce. Take the line."], "down"),
        "co_12": (["Twelve, craps. Boxcars. Line away.", "Midnight! Twelve craps, line away."], "down"),
        "out": (["Seven out. Line away.", "Seven out! Pay the don'ts, take the line.", "Seven... out. Line away."], "down"),
        "pt_2": (["Two, aces! Field pays double.", "Aces, two craps. Field's a winner."], "calm"),
        "pt_3": (["Three, ace-deuce. Field's a winner.", "Three craps, ace-deuce."], "calm"),
        "pt_11": (["Yo eleven! Yo!", "Eleven, yo! Pay the yo!"], "cheer"),
        "pt_12": (["Twelve, boxcars! Field pays triple.", "Boxcars! Midnight, twelve."], "calm"),
        "num_5": (["Five, no field five.", "Five. Fever five."], "calm"),
        "num_9": (["Nine, center field nine.", "Nine. Nina from Pasadena."], "calm"),
        "hard_4": (["Four, hard four! Little Joe the hard way.", "Hard four! Two and two."], "cheer"),
        "hard_6": (["Six, hard six! Came the hard way.", "Hard six! Threes, the hard way."], "cheer"),
        "hard_8": (["Eight, hard eight! Came the hard way.", "Hard eight! Fours, the hard way."], "cheer"),
        "hard_10": (["Ten, hard ten! Came the hard way.", "Hard ten! Fives, the hard way."], "cheer"),
        "easy_4": (["Four, easy four. Little Joe.", "Easy four."], "calm"),
        "easy_6": (["Six, easy six.", "Six. Easy six, no field."], "calm"),
        "easy_8": (["Eight, easy eight.", "Eight. Easy eight, no field."], "calm"),
        "easy_10": (["Ten, easy ten.", "Ten. Easy ten, field winner."], "calm"),
        "bonus_Small": (["Small! All the small numbers! Pay the Small!"], "hype"),
        "bonus_Tall": (["Tall! All the tall numbers! Pay the Tall!"], "hype"),
        "bonus_All": (["Make 'em all! All ten numbers! What a hand!"], "hype"),
        "coming_out": (["Coming out!", "Same shooter, coming out!", "Dice are coming out!"], "cheer"),
        "new_shooter": (["New shooter, coming out.", "Next shooter. Coming out."], "calm"),
        "good_roll": (["Great roll, shooter!", "Nice hand, shooter."], "cheer"),
    }
    for n in POINT_NUMBERS:
        w = WORDS[n]
        lines[f"co_point_{n}"] = ([f"{w.capitalize()}. The point is {w}. Mark it!", f"{w.capitalize()}, {w}'s the point!",
                                   f"Shooter's point is {w}. Mark the {w}!"], "cheer")
        lines[f"made_{n}"] = ([f"Winner! {w.capitalize()}, the point! Pay the line!", f"{w.capitalize()}, winner! Front line winner!",
                              f"Winner winner! {w.capitalize()} the point, pay the line!"], "hype")
        if n % 2 == 0:
            lines[f"made_hard_{n}"] = ([f"{w.capitalize()} the hard way! Winner! Pay the line, pay the hardway!"], "hype")
    for n, w in WORDS.items():
        plural = "sixes" if n == 6 else w + "s"
        lines[f"b2b_{n}"] = ([f"Back to back {plural}!", f"{plural.capitalize()}, back to back!"], "cheer")
    for k in HOT_ROLLS:
        lines[f"hot_{k}"] = ([f"Shooter's hot! {k} rolls and counting!", f"{k} rolls! Keep 'em coming, shooter!"], "hype")
    return lines


LINES = _build_lines()

def _build_creature_lines():
    """The Gollum-ish stickman's own words. Any line missing here falls back to the normal one."""
    lines = {
        "no_roll": (["No roll! It must touch the wall, precious, the wall!", "Short, short! Nasty throw. No roll!"], "calm"),
        "co_7": (["Seven! We wins, precious, front line wins!", "Seven, seven! Pay the line, [gulps] gollum, gollum!"], "hype"),
        "co_11": (["Eleven! Lucky, lucky! Pay the line!", "Yo eleven! Shiny, shiny eleven!"], "hype"),
        "co_2": (["Two little aces. Craps. We hates it!", "Aces! Craps, precious. Line goes away."], "down"),
        "co_3": (["Three. Craps, precious. Takes our line, it does.", "Ace-deuce, craps! Tricksy dice."], "down"),
        "co_12": (["Twelve! Boxcars! Craps, nasty craps!", "Midnight twelve. Craps. [mischievously] Tricksy, tricksy dice."], "down"),
        "out": (["[crying] Seven out! No, no, no! It's gone, precious, all gone!",
                 "Seven... out. We hates sevens. Hates them forever!",
                 "Seven out! Thief! Takes our line, it does!"], "down"),
        "pt_2": (["Two! Field pays double, precious, double!"], "calm"),
        "pt_3": (["Three, ace-deuce. Field wins, yes."], "calm"),
        "pt_11": (["Yo eleven! Shiny eleven!", "Eleven! We likes eleven."], "cheer"),
        "pt_12": (["Twelve! Field pays triple, precious, triple!"], "calm"),
        "num_5": (["Five. No field five. Five little fingers.", "Five, precious. Just five."], "calm"),
        "num_9": (["Nine. Field nine, precious.", "Nine! We counts it. Nine."], "calm"),
        "bonus_Small": (["All the small ones, every one! Pay the Small, precious!"], "hype"),
        "bonus_Tall": (["All the tall ones! The Tall pays, yes, yes!"], "hype"),
        "bonus_All": (["[laughs] We makes them all! Every number! Ours, precious, ours!"], "hype"),
        "coming_out": (["Coming out, precious. Throw it, throw it!", "Dice is coming out. Careful, careful."], "cheer"),
        "new_shooter": (["[whispers] New shooter. We doesn't trust them, no.", "Next one coming out. Sneaky shooter."], "calm"),
        "good_roll": (["Good hand, precious. Long, long hand."], "cheer"),
    }
    pairs = {4: "twos", 6: "threes", 8: "fours", 10: "fives"}
    for n, pair in pairs.items():
        w = WORDS[n]
        lines[f"hard_{n}"] = ([f"Hard {w}! Two {pair}, together, the hard way, precious!"], "cheer")
        lines[f"easy_{n}"] = ([f"{w.capitalize()}, easy {w}. Easy, easy."], "calm")
    for n in POINT_NUMBERS:
        w = WORDS[n]
        lines[f"co_point_{n}"] = ([f"{w.capitalize()}! The point is {w}, precious. Mark it, mark it!",
                                   f"{w.capitalize()} is the point. We remembers it, yes."], "cheer")
        lines[f"made_{n}"] = ([f"{w.capitalize()}! Point made! We wins, we wins, precious!",
                              f"Winner {w}! Pay the line, [gulps] gollum, gollum!"], "hype")
        if n % 2 == 0:
            lines[f"made_hard_{n}"] = ([f"Hard {w}, the point! Pay the line and the hardway, precious!"], "hype")
    for n, w in WORDS.items():
        lines[f"b2b_{n}"] = ([f"{w.capitalize()} again! Back to back, precious!"], "cheer")
    for k in HOT_ROLLS:
        lines[f"hot_{k}"] = ([f"{k} rolls! Shooter is hot, precious, hot, hot!"], "hype")
    return lines


def shlur(text):
    """'Six, the hard way' -> 'Shix, the hard way'. Keeps 'sh' as it is and the capital letters."""
    return re.sub(r"[sS](?![hH])", lambda m: "Sh" if m.group() == "S" else "sh", text)


def strip_tags(text):
    """Drop ElevenLabs stage directions like '[whispers] ', which other voices would read out."""
    return re.sub(r"\[[^\]]*\]\s*", "", text)


def hiss(text):
    """'Yes, precious' -> 'Yesss, preciousss': draws out an s at the end of a word."""
    return re.sub(r"(?<=\w)s\b", "sss", text)


# How each mood is spoken: (rate %, pitch Hz, volume %)
MOODS = {"hype": (14, 14, 10), "cheer": (8, 6, 0), "calm": (4, 0, 0), "down": (-4, -8, -5)}
# Voice name -> (edge-tts voice, rate shift %, pitch shift Hz, how the words are bent before speaking)
VOICES = {"Andrew": ("en-US-AndrewNeural", 0, 0, None),
          "Brian": ("en-US-BrianNeural", 0, 0, None),
          "Ava": ("en-US-AvaNeural", 0, 0, None),
          "Aria": ("en-US-AriaNeural", 0, 0, None),
          "Shean (Connery-ish)": ("en-GB-RyanNeural", -10, -14, shlur),
          "Precious (Gollum-ish)": ("en-GB-ThomasNeural", 6, 22, hiss)}
CHARACTER_LINES = {"Precious (Gollum-ish)": _build_creature_lines()}
DEFAULT_VOICE = "Andrew"


def voice_lines(voice):
    return {**LINES, **CHARACTER_LINES.get(voice, {})}


def croupier_call(throws, voice=DEFAULT_VOICE):
    """What the stickman says about the last throw, as a list of (text, mood) pieces played one after another."""
    last = throws[-1]
    variant = len(throws)                   # rotates through each line's variants, so the same words don't repeat
    keys = []
    if not last["back_wall"]:
        keys.append("no_roll")
    else:
        point_before = shooter_status(throws[:-1])[0]
        point_after, rolls_after, last_run = shooter_status(throws)
        total = last["total"]
        hard = last["die1"] == last["die2"]
        if point_before is None:
            if total in POINT_NUMBERS:
                keys.append(f"co_point_{total}")
            else:
                keys += [f"co_{total}", "coming_out"]
        elif total == 7:
            keys.append("out")
            if last_run >= HOT_ROLLS[0]:
                keys.append("good_roll")
            keys.append("new_shooter")
        elif total == point_before:
            keys += [f"made_hard_{total}" if hard and total % 2 == 0 else f"made_{total}", "coming_out"]
        elif total in (4, 6, 8, 10):
            keys.append(f"{'hard' if hard else 'easy'}_{total}")
        elif total in (5, 9):
            keys.append(f"num_{total}")
        else:
            keys.append(f"pt_{total}")

        previous = next((t for t in reversed(throws[:-1]) if t["session"] == last["session"] and t["back_wall"]), None)
        if previous and previous["total"] == total and keys[0] != "out":
            keys.insert(1, f"b2b_{total}")
        if rolls_after in HOT_ROLLS:
            keys.append(f"hot_{rolls_after}")
        wins = [w for w in bonus_status(throws)[4] if w["time"] == last["timestamp"]]
        if wins:
            keys.insert(1, f"bonus_{wins[-1]['kind']}")
    lines = voice_lines(voice)
    pieces = []
    for key in keys[:3]:
        variants, mood = lines[key]
        i = variant % len(variants)
        pieces.append((variants[i], mood, f"{key}_{i}"))
    return pieces


def pack_name(voice):
    """The folder name a voice's recorded lines live in: 'Precious (Gollum-ish)' -> 'Precious_Gollum_ish'."""
    return re.sub(r"[^A-Za-z0-9]+", "_", voice).strip("_")


def stickman_call(result, variant, voice):
    """The stickman's words for a roll (None = a no-roll), as (text, mood) pieces from the tracker's lines."""
    if result is None:
        keys = ["no_roll"]
    else:
        total, point = result["total"], result["point_before"]
        if point is None:
            keys = [f"co_point_{total}"] if total in POINT_NUMBERS else [f"co_{total}", "coming_out"]
        elif total == 7:
            keys = ["out"] + (["good_roll"] if result["last_run"] >= HOT_ROLLS[0] else []) + ["new_shooter"]
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
        if result["shooter_rolls"] in HOT_ROLLS:
            keys.append(f"hot_{result['shooter_rolls']}")
        if result["bonus_wins"]:
            keys.insert(1, f"bonus_{result['bonus_wins'][-1]}")
    lines = voice_lines(voice)
    pieces = []
    for key in keys[:3]:
        variants, mood = lines[key]
        i = variant % len(variants)
        pieces.append((variants[i], mood, f"{key}_{i}"))
    return pieces
