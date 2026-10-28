"""
Tests for the web server:   pip install -r requirements-web.txt pytest   then   python -m pytest tests
"""

import os
import re
import sys
import tempfile

DATA = tempfile.mkdtemp(prefix="craps-test-")
os.environ["CRAPS_DATA_DIR"] = DATA
os.environ["CRAPS_TTS"] = "0"
os.environ["CRAPS_SMTP_HOST"] = ""
os.environ["CRAPS_TURNSTILE_SECRET"] = ""
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from web import app as web_app, config, db, mail  # noqa: E402

SENT = []
HEADERS = {"X-Requested-With": "craps"}


@pytest.fixture(autouse=True)
def capture_mail(monkeypatch):
    SENT.clear()
    monkeypatch.setattr(config, "LINKS_PER_IP", 1000)       # every test signs in from the same address
    monkeypatch.setattr(mail, "send", lambda to, subject, text, html=None: SENT.append((to, subject, text)))


def client():
    c = TestClient(web_app.app)
    c.headers.update(HEADERS)
    return c


def token_from_mail():
    return re.search(r"token=([\w-]+)", SENT[-1][2]).group(1)


def signed_in_client(email):
    c = client()
    c.__enter__()
    assert c.post("/api/auth/request", json={"email": email}).status_code == 200
    r = c.post("/api/auth/verify", json={"token": token_from_mail()})
    assert r.status_code == 200, r.text
    return c


def me(c):
    r = c.get("/api/me")
    assert r.status_code == 200, r.text
    return r.json()


def test_needs_sign_in_and_header():
    with client() as c:
        assert c.get("/api/me").status_code == 401
        plain = TestClient(web_app.app)
        assert plain.post("/api/auth/request", json={"email": "a@b.co"}).status_code == 403


def test_sign_in_link_works_once():
    with client() as c:
        assert c.post("/api/auth/request", json={"email": "Once@Example.com"}).status_code == 200
        token = token_from_mail()
        assert SENT[-1][0] == "once@example.com"
        assert c.get("/api/auth/link", params={"token": token}).json()["email"] == "once@example.com"
        assert c.post("/api/auth/verify", json={"token": token}).status_code == 200
        assert me(c)["email"] == "once@example.com"
        assert c.post("/api/auth/verify", json={"token": token}).status_code == 400


def test_rate_limit_per_email():
    with client() as c:
        for _ in range(config.LINKS_PER_EMAIL):
            assert c.post("/api/auth/request", json={"email": "busy@example.com"}).status_code == 200
        r = c.post("/api/auth/request", json={"email": "busy@example.com"})
        assert r.status_code == 400 and "wait" in r.json()["error"]


def test_bad_email():
    with client() as c:
        assert c.post("/api/auth/request", json={"email": "not an email"}).status_code == 400


def test_new_account_names_its_seat():
    c = signed_in_client("newbie@example.com")
    info = me(c)
    assert info["name"] == "" and len(info["players"]) == 1 and info["players"][0]["is_self"]
    assert c.post("/api/me", json={"name": "Kenn"}).status_code == 200
    assert me(c)["players"][0]["name"] == "Kenn"


def test_pass_line_and_undo():
    c = signed_in_client("table@example.com")
    pid = me(c)["players"][0]["id"]
    r = c.post(f"/api/players/{pid}/change", json={"action": "bet", "zone": "pass", "cents": 500})
    assert r.status_code == 200 and r.json()["table"]["bankroll"] == 9500
    r = c.post(f"/api/players/{pid}/roll", json={"die1": 3, "die2": 3})
    assert r.status_code == 200
    view = c.get(f"/api/players/{pid}/table").json()
    assert view["point"] == 6 and view["last_roll"]["state"].startswith("Point is 6")
    # odds behind the line, then make the point
    c.post(f"/api/players/{pid}/change", json={"action": "bet", "zone": "pass_odds", "cents": 2500})
    c.post(f"/api/players/{pid}/roll", json={"die1": 2, "die2": 4})
    view = c.get(f"/api/players/{pid}/table").json()
    assert view["point"] is None and view["bankroll"] == 9500 + 500 + 3000   # flat stays up, odds back with 6:5
    # undo puts the bets back
    assert c.post(f"/api/players/{pid}/undo").status_code == 200
    view = c.get(f"/api/players/{pid}/table").json()
    assert view["point"] == 6 and {b["spot"]: b["odds"] for b in view["bets"]}["pass"] == 2500


def test_fix_last_resettles():
    c = signed_in_client("fix@example.com")
    pid = me(c)["players"][0]["id"]
    c.post(f"/api/players/{pid}/change", json={"action": "bet", "zone": "pass", "cents": 500})
    c.post(f"/api/players/{pid}/roll", json={"die1": 1, "die2": 1})            # craps: the line loses
    assert c.get(f"/api/players/{pid}/table").json()["bankroll"] == 9500
    r = c.post(f"/api/players/{pid}/fix", json={"die1": 5, "die2": 2})        # it was really a 7
    assert r.status_code == 200 and "Fixed" in r.json()["message"]
    view = c.get(f"/api/players/{pid}/table").json()
    assert view["bankroll"] == 10000 and view["on_table"] == 500 and view["last_roll"]["net"] == 500   # won, stays up


def test_refused_bet_says_why():
    c = signed_in_client("refuse@example.com")
    pid = me(c)["players"][0]["id"]
    r = c.post(f"/api/players/{pid}/change", json={"action": "bet", "zone": "come", "cents": 500})
    assert r.status_code == 409 and "Come bets need a point" in r.json()["error"]
    r = c.post(f"/api/players/{pid}/change", json={"action": "bet", "zone": "pass", "cents": 333})
    assert r.status_code == 404


def test_move_bet_and_hop():
    c = signed_in_client("move@example.com")
    pid = me(c)["players"][0]["id"]
    c.post(f"/api/players/{pid}/change", json={"action": "bet", "zone": "field", "cents": 500})
    r = c.post(f"/api/players/{pid}/change", json={"action": "move", "zone": "field", "to": "any7"})
    assert r.status_code == 200 and [b["spot"] for b in r.json()["table"]["bets"]] == ["any7"]
    r = c.post(f"/api/players/{pid}/change", json={"action": "bet", "zone": "hop", "hop": "2-5", "cents": 100})
    assert "hop_2_5" in [b["spot"] for b in r.json()["table"]["bets"]]


def test_tracker_log_and_stats():
    c = signed_in_client("tracker@example.com")
    pid = me(c)["players"][0]["id"]
    for d1, d2 in [(3, 4), (2, 2), (6, 5), (1, 2)]:
        r = c.post(f"/api/players/{pid}/roll",
                   json={"die1": d1, "die2": d2, "mode": "tracker", "dice_set": "3-V", "position": "Stick left",
                         "local_time": "2026-10-28T20:00:00"})
        assert r.status_code == 200
    view = c.get(f"/api/players/{pid}/tracker", params={"dice_set": "3-V", "position": "Stick left"}).json()
    assert view["rows"][0]["throws"] == 4 and view["rows"][1]["group"] == "3-V"
    assert view["chart"]["n"] == 4 and view["last"]["dice"] == [1, 2]
    # tracker throws don't touch the table, and undo removes them
    assert c.get(f"/api/players/{pid}/table").json()["bankroll"] == 10000
    c.post(f"/api/players/{pid}/undo")
    assert c.get(f"/api/players/{pid}/tracker").json()["rows"][0]["throws"] == 3


def test_guests_and_claim():
    host = signed_in_client("host@example.com")
    host.post("/api/me", json={"name": "Host"})
    gid = host.post("/api/players", json={"name": "Sam"}).json()["id"]
    host.post(f"/api/players/{gid}/roll", json={"die1": 3, "die2": 4, "mode": "tracker"})
    host.post(f"/api/players/{gid}/roll", json={"die1": 6, "die2": 6, "mode": "tracker"})
    r = host.post(f"/api/players/{gid}/invite", json={"email": "sam@example.com"})
    assert r.status_code == 200 and SENT[-1][0] == "sam@example.com"
    token = token_from_mail()
    with client() as sam:
        info = sam.get("/api/auth/link", params={"token": token}).json()
        assert info["purpose"] == "claim" and info["guest"] == "Sam" and info["host"] == "Host"
        r = sam.post("/api/auth/verify", json={"token": token})
        assert r.json()["claimed"] == "Sam"
        mine = me(sam)
        assert mine["name"] == "Sam" and mine["players"][0]["throws"] == 2
    assert [p["name"] for p in me(host)["players"]] == ["Host"]
    # someone else's player is off limits
    other = signed_in_client("other@example.com")
    assert other.get(f"/api/players/{me(host)['players'][0]['id']}/table").status_code == 404


def test_reset_and_export_and_delete():
    c = signed_in_client("leaver@example.com")
    pid = me(c)["players"][0]["id"]
    c.post(f"/api/players/{pid}/roll", json={"die1": 4, "die2": 4, "dice_set": "Hardway 4-2"})
    r = c.post(f"/api/players/{pid}/change", json={"action": "reset", "cents": 50000})
    assert r.json()["table"]["bankroll"] == 50000 and not r.json()["table"]["can_undo"]
    csv_text = c.get("/api/export.csv").text
    assert csv_text.splitlines()[0].startswith("timestamp,session,dice_set") and "Hardway 4-2" in csv_text
    assert c.post("/api/me/delete", json={"confirm": "nope"}).status_code == 400
    assert c.post("/api/me/delete", json={"confirm": "DELETE"}).status_code == 200
    assert db.one("SELECT COUNT(*) FROM users WHERE email = 'leaver@example.com'")[0] == 0
    assert db.one("SELECT COUNT(*) FROM throws WHERE player_id = ?", pid)[0] == 0


def test_stickman_call_and_voice_fallback():
    c = signed_in_client("voice@example.com")
    pid = me(c)["players"][0]["id"]
    c.post("/api/me", json={"settings": {"voice": "Precious (Gollum-ish)"}})
    call = c.post(f"/api/players/{pid}/roll", json={"die1": 5, "die2": 6}).json()["call"]
    assert [p["clip"][:6] for p in call] == ["co_11_", "coming"] and "[" not in call[1]["spoken"]
    # no recording and edge-tts turned off: the browser speaks it instead
    assert c.get(f"/api/voice/Precious_Gollum_ish/{call[0]['clip']}.mp3").status_code == 404
    assert c.get("/api/voice/Precious_Gollum_ish/not_a_line_0.mp3").status_code == 404
