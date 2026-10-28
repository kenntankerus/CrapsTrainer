"""The stickman's personal calls, the crowd's reactions, and choosing a voice engine."""

import os

from test_web import me, signed_in_client

from web import calls, config, db, voice

PERSONAL = "voice-premium@example.com"


def test_money_and_bets_in_words():
    assert calls.money_words(500) == "five dollars"
    assert calls.money_words(583) == "five eighty-three"
    assert calls.money_words(100) == "one dollar"
    assert calls.money_words(4503) == "forty-five dollars and three cents"
    assert calls.money_words(1234500) == "twelve thousand three hundred forty-five dollars"
    assert calls.bet_words("hard_8") == "the hard eight"
    assert calls.bet_words("come_10") == "the come bet on the ten"
    assert calls.bet_words("hop_1_6") == "the hop, one six"
    assert calls.bet_words("pass") == "the line"


def roll_result(events, **extra):
    return {"events": events, "bonus_wins": [], "seven_out": False, "net": sum(e[1] for e in events), **extra}


def test_personal_calls():
    assert calls.personal_call(roll_result([("hard_8", 4500, "wins")]), "Kenn") == \
        ("Pay the hard eight, forty-five dollars.", "cheer")
    assert calls.personal_call(roll_result([("place_6", 700, "wins"), ("field", -500, "loses")]), "Kenn") == \
        ("Pay the six, seven dollars.", "cheer")
    assert calls.personal_call(roll_result([("place_6", 700, "wins"), ("pass", 500, "wins")]), "Kenn") == \
        ("Twelve dollars on the roll, Kenn.", "cheer")
    assert calls.personal_call(roll_result([("pass", -2500, "loses"), ("place_8", -1200, "loses")], seven_out=True),
                               "Kenn") == ("That's thirty-seven dollars off the table, Kenn.", "down")
    assert calls.personal_call(roll_result([("field", -500, "loses")]), "Kenn") is None
    assert calls.personal_call(None, "Kenn") is None
    assert "precious" in calls.personal_call(roll_result([("yo", 7500, "wins")]), "Kenn", "Precious (Gollum-ish)")[0]


def test_crowd_reactions():
    assert calls.reaction(["made_6", "coming_out"]) == "cheer"
    assert calls.reaction(["made_hard_8", "coming_out"]) == "big_cheer"
    assert calls.reaction(["out", "new_shooter"]) == "groan"
    assert calls.reaction(["co_12", "coming_out"]) == "aww"
    assert calls.reaction(["num_5"]) is None
    assert calls.reaction(["num_5"], net=20000) == "big_cheer"


def test_engines_and_personal_engine(monkeypatch):
    monkeypatch.setattr(config, "TTS", True)
    assert voice.engines("Ava") == ["edge"]
    assert voice.personal_engine("Ava", premium=False) == "edge"        # a free engine: everyone gets the calls
    monkeypatch.setattr(config, "ELEVENLABS_KEY", "key")
    monkeypatch.setattr(config, "ELEVENLABS_VOICES", {"Ava": "voice-id"})
    assert voice.engines("Ava") == ["elevenlabs", "edge"]
    assert voice.personal_engine("Ava", premium=True) == "elevenlabs"
    assert voice.personal_engine("Ava", premium=False) is None         # not a mismatched voice, and not on your bill
    assert voice.engines("Brian") == ["edge"]


def test_daily_cap(monkeypatch):
    db.init()
    monkeypatch.setattr(config, "ELEVENLABS_DAILY_CHARS", 100)
    db.connect().execute("DELETE FROM tts_usage")
    assert voice.spend(60) is True
    assert voice.spend(60) is False
    assert voice.spend(40) is True
    assert voice.spend(1) is False


def test_roll_gives_reaction_and_personal_line(monkeypatch):
    c = signed_in_client(PERSONAL)
    c.post("/api/me", json={"name": "Kenn", "settings": {"voice": "Ava"}})
    pid = me(c)["players"][0]["id"]
    c.post(f"/api/players/{pid}/change", json={"action": "bet", "zone": "pass", "cents": 500})
    r = c.post(f"/api/players/{pid}/roll", json={"die1": 3, "die2": 4}).json()      # a natural
    assert r["reaction"] == "cheer" and all(p["url"].startswith("/api/voice/Ava/") for p in r["call"])
    assert not any(p.get("dynamic") for p in r["call"])                 # no engine here, so no personal call

    made = []

    async def fake_make(engine, text, mood, name, fast=False):
        made.append((engine, text, fast))
        path = os.path.join(config.DATA_DIR, "fake.mp3")
        open(path, "wb").write(b"ID3")
        return path

    monkeypatch.setattr(config, "TTS", True)
    monkeypatch.setattr(voice, "make", fake_make)
    r = c.post(f"/api/players/{pid}/roll", json={"die1": 5, "die2": 6}).json()      # yo eleven: the line wins again
    line = [p for p in r["call"] if p.get("dynamic")]
    assert line and line[0]["text"] == "Pay the line, five dollars."
    assert c.get(line[0]["url"]).content == b"ID3" and made == [("edge", "Pay the line, five dollars.", True)]
    assert c.get("/api/voice/Ava/line/not-a-token").status_code == 404

    c.post("/api/me", json={"settings": {"personal": False}})
    r = c.post(f"/api/players/{pid}/roll", json={"die1": 6, "die2": 5}).json()
    assert not any(p.get("dynamic") for p in r["call"])


def test_voice_clip_list():
    c = signed_in_client(PERSONAL)
    urls = c.get("/api/voice/Precious_Gollum_ish/clips").json()["urls"]
    assert "/api/voice/Precious_Gollum_ish/out_0" in urls and len(urls) == len(voice.clip_names("Precious (Gollum-ish)"))
    assert c.get("/api/voice/Nobody/clips").status_code == 404
    assert c.get("/api/sounds").json() == {"sounds": []}
