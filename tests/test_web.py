"""
Tests for the web server:   pip install -r requirements-web.txt pytest   then   python -m pytest tests
"""

import re

from fastapi.testclient import TestClient

from conftest import SENT
from web import app as web_app, config, db

HEADERS = {"X-Requested-With": "craps"}


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


def test_pages_and_scripts_are_checked_for_changes():
    # a browser holding an old app.js for hours was why signing in from another device didn't work
    c = client()
    for path in ("/", "/static/app.js", "/static/style.css", "/manifest.webmanifest"):
        assert c.get(path).headers["cache-control"] == "no-cache", path


def test_sign_in_link_works_once():
    with client() as c:
        assert c.post("/api/auth/request", json={"email": "Once@Example.com"}).status_code == 200
        token = token_from_mail()
        assert SENT[-1][0] == "once@example.com"
        assert c.get("/api/auth/link", params={"token": token}).json()["email"] == "once@example.com"
        assert c.post("/api/auth/verify", json={"token": token}).status_code == 200
        assert me(c)["email"] == "once@example.com"
        assert c.post("/api/auth/verify", json={"token": token}).status_code == 400


def test_link_opened_elsewhere_signs_in_the_asking_device():
    with client() as asker, client() as phone:
        r = asker.post("/api/auth/request", json={"email": "two@example.com"},
                       headers={"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) Firefox/140.0"})
        code = r.json()["code"]
        assert re.fullmatch(r"\d{4}", code)
        assert asker.post("/api/auth/wait").json() == {"state": "waiting", "code": code}    # a reloaded page shows it again
        token = token_from_mail()
        info = phone.get("/api/auth/link", params={"token": token}).json()
        assert info["asked_on"] == {"device": "Firefox on Linux", "code": code}
        r = phone.post("/api/auth/verify", json={"token": token, "where": "asker"})
        assert r.status_code == 200 and r.json()["where"] == "asker"
        assert phone.get("/api/me").status_code == 401             # the phone only approved it
        assert asker.post("/api/auth/wait").json()["state"] == "signed_in"
        assert me(asker)["email"] == "two@example.com"
        assert asker.post("/api/auth/wait").json()["state"] == "signed_in"      # picked up once, then just signed in
        with client() as third:                                    # nobody else can pick it up
            assert third.post("/api/auth/wait").json()["state"] == "expired"


def test_link_opened_on_the_asking_device_signs_in_here():
    with client() as c:
        c.post("/api/auth/request", json={"email": "same@example.com"})
        token = token_from_mail()
        assert "asked_on" not in c.get("/api/auth/link", params={"token": token}).json()
        assert c.post("/api/auth/verify", json={"token": token}).status_code == 200
        assert c.post("/api/auth/wait").json()["state"] == "signed_in"
    with client() as asker, client() as other:                    # or the other device signs itself in instead
        asker.post("/api/auth/request", json={"email": "here@example.com"})
        assert other.post("/api/auth/verify", json={"token": token_from_mail(), "where": "here"}).status_code == 200
        assert me(other)["email"] == "here@example.com"
        assert asker.post("/api/auth/wait").json()["state"] == "expired"
        assert asker.get("/api/me").status_code == 401


def test_rate_limit_per_email(monkeypatch):
    monkeypatch.setattr(config, "LINKS_PER_EMAIL", 3)
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


def test_table_counts_the_hand():
    c = signed_in_client("counter@example.com")
    pid = me(c)["players"][0]["id"]
    table = c.get(f"/api/players/{pid}/table").json()
    assert table["shooter_rolls"] == 0 and table["last_run"] is None
    for die1, die2 in ((4, 4), (2, 3), (3, 4)):          # point 8, a roll, seven out
        c.post(f"/api/players/{pid}/roll", json={"die1": die1, "die2": die2})
    table = c.get(f"/api/players/{pid}/table").json()
    assert table["shooter_rolls"] == 0 and table["last_run"] == 3
    c.post(f"/api/players/{pid}/roll", json={"die1": 5, "die2": 6})
    table = c.get(f"/api/players/{pid}/table").json()
    assert table["shooter_rolls"] == 1 and table["last_run"] == 3


def test_virtual_roll_settles_bets_but_skips_the_stats():
    c = signed_in_client("virtual@example.com")
    pid = me(c)["players"][0]["id"]
    c.post(f"/api/players/{pid}/change", json={"action": "bet", "zone": "field", "cents": 500})
    r = c.post(f"/api/players/{pid}/virtual")
    assert r.status_code == 200
    die1, die2 = r.json()["dice"]
    assert "Not counted" in r.json()["message"]
    view = c.get(f"/api/players/{pid}/table").json()
    assert view["last_roll"]["dice"] == [die1, die2] and view["rolls"][0]["virtual"] is True
    assert view["last_roll"]["events"][0]["spot"] == "field"          # the field bet was settled
    assert c.get(f"/api/players/{pid}/tracker").json()["recent"] == []
    assert me(c)["players"][0]["throws"] == 0
    assert c.get("/api/export.csv").text.strip().count("\n") == 0      # just the header
    other = 1 if die1 != 1 else 2
    c.post(f"/api/players/{pid}/fix", json={"die1": other, "die2": die2})
    assert me(c)["players"][0]["throws"] == 0                       # fixed, and still virtual
    assert c.post(f"/api/players/{pid}/undo").status_code == 200
    view = c.get(f"/api/players/{pid}/table").json()                # the field bet is back up, as before the roll
    assert view["bankroll"] == 9500 and [b["spot"] for b in view["bets"]] == ["field"]


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


def test_custom_hold_is_logged_as_the_set_it_is():
    c = signed_in_client("custom@example.com")
    pid = me(c)["players"][0]["id"]
    messages = [c.post(f"/api/players/{pid}/roll", json={"die1": 3, "die2": 3, "mode": "tracker",
                                                         "dice_set": f"Custom {hold}"}).json()["message"]
                for hold in ("L3-2 R3-2", "L5-1 R5-1")]
    assert messages == ["Logged 3-3 on Hardway 3-2", "Logged 3-3 on Custom L5-1 R5-1"]
    names = {row["group"] for row in c.get(f"/api/players/{pid}/tracker").json()["rows"]}
    assert {"Hardway 3-2", "Custom L5-1 R5-1"} <= names
    view = c.get(f"/api/players/{pid}/tracker", params={"dice_set": "Custom L3-2 R3-2"}).json()
    assert view["chart"]["n"] == 1                                 # the filter finds it under its real name
    assert "Custom" in me(c)["sets"]
    # the hold picked for each set is kept, and only holds that are that set
    c.post("/api/me", json={"settings": {"holds": {"Straight Sixes": "6-5", "Hardway": "6-5"}}})
    c.post("/api/me", json={"settings": {"holds": {"Hardway": "3-2"}}})
    assert me(c)["settings"]["holds"] == {"Straight Sixes": "6-5", "Hardway": "3-2"}


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


def test_guest_emails_are_capped_per_account(monkeypatch):
    monkeypatch.setattr(config, "INVITES_PER_DAY", 2)
    host = signed_in_client("inviter@example.com")
    gid = host.post("/api/players", json={"name": "Pat"}).json()["id"]
    for n in range(2):
        assert host.post(f"/api/players/{gid}/invite", json={"email": f"pat{n}@example.com"}).status_code == 200
    r = host.post(f"/api/players/{gid}/invite", json={"email": "pat9@example.com"})
    assert r.status_code >= 400 and "tomorrow" in r.json()["error"]
    assert SENT[-1][0] == "pat1@example.com"
    # another account has its own allowance
    other = signed_in_client("inviter2@example.com")
    oid = other.post("/api/players", json={"name": "Lee"}).json()["id"]
    assert other.post(f"/api/players/{oid}/invite", json={"email": "lee@example.com"}).status_code == 200


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


def test_feedback_is_kept_and_emailed(monkeypatch):
    monkeypatch.setattr(config, "FEEDBACK_EMAIL", "owner@example.com")
    monkeypatch.setattr(config, "FEEDBACK_PER_DAY", 2)
    c = signed_in_client("tester@example.com")
    c.post("/api/me", json={"name": "Tess"})
    assert c.post("/api/feedback", json={"kind": "praise", "text": "hi"}).status_code == 400
    assert c.post("/api/feedback", json={"kind": "bug", "text": "   "}).status_code == 400
    r = c.post("/api/feedback", json={"kind": "bug", "text": "Undo did nothing", "context": "Tab: table"})
    assert r.status_code == 200, r.text
    to, subject, text = SENT[-1]
    assert to == "owner@example.com" and "bug report" in subject and "Tess" in subject
    assert "Undo did nothing" in text and "Tab: table" in text and "tester@example.com" in text
    # a feature request doesn't carry the page's details
    assert c.post("/api/feedback", json={"kind": "feature", "text": "Show odds", "context": "x"}).status_code == 200
    rows = db.all_rows("SELECT f.kind, f.context FROM feedback f JOIN users u ON u.id = f.user_id "
                       "WHERE u.email = 'tester@example.com' ORDER BY f.id")
    assert [(r["kind"], r["context"]) for r in rows] == [("bug", "Tab: table"), ("feature", "")]
    assert c.post("/api/feedback", json={"kind": "feature", "text": "More"}).status_code == 429
    # it goes with the account
    assert c.post("/api/me/delete", json={"confirm": "DELETE"}).status_code == 200
    assert db.one("SELECT COUNT(*) FROM feedback")[0] == 0


def test_how_it_works_is_shown_once():
    c = signed_in_client("newcomer@example.com")
    assert not me(c)["settings"].get("welcomed")
    c.post("/api/me", json={"settings": {"welcomed": True}})
    assert me(c)["settings"]["welcomed"] is True


def test_stickman_call_and_voice_fallback():
    c = signed_in_client("voice@example.com")
    pid = me(c)["players"][0]["id"]
    c.post("/api/me", json={"settings": {"voice": "Precious (Gollum-ish)"}})
    call = c.post(f"/api/players/{pid}/roll", json={"die1": 5, "die2": 6}).json()["call"]
    assert [p["clip"][:6] for p in call] == ["co_11_", "coming"] and "[" not in call[1]["spoken"]
    # no recording and edge-tts turned off: the browser speaks it instead
    assert c.get(f"/api/voice/Precious_Gollum_ish/{call[0]['clip']}.mp3").status_code == 404
    assert c.get("/api/voice/Precious_Gollum_ish/not_a_line_0.mp3").status_code == 404
