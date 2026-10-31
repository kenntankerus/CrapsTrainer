"""
Signing in with an emailed link: no passwords. Asking for a link makes an account the first
time an address is used. The link signs one device in for SESSION_DAYS since it was last used.
Only hashes of the link and session tokens are stored, so a copy of the database can't sign anyone in.

The link often opens somewhere else than where it was asked for: email read on a phone, or in the
email app's own browser. So the asking page keeps a secret of its own (the WAIT_COOKIE) and waits;
whoever opens the link can send the sign-in back to it. The asking page shows a short code, and the
link's page shows the same code, so nobody approves a stranger's device that asked for their address.
"""

import hashlib
import json
import re
import secrets
import time
import urllib.parse
import urllib.request
from datetime import datetime

from . import config, db

COOKIE = "craps_session"
WAIT_COOKIE = "craps_wait"           # the asking device's secret, while it waits for its link to be opened
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class AuthError(Exception):
    """A sign-in that can't go ahead, with the reason to show."""


def _hash(token):
    return hashlib.sha256(token.encode()).hexdigest()


def clean_email(email):
    email = (email or "").strip().lower()
    if len(email) > 254 or not EMAIL_RE.match(email):
        raise AuthError("That doesn't look like an email address.")
    return email


def check_turnstile(token, ip):
    """True when Cloudflare says the visitor is a person, or when the bot check is turned off."""
    if not config.TURNSTILE_SECRET:
        return True
    if not token:
        return False
    body = urllib.parse.urlencode({"secret": config.TURNSTILE_SECRET, "response": token, "remoteip": ip}).encode()
    try:
        with urllib.request.urlopen("https://challenges.cloudflare.com/turnstile/v0/siteverify", body, timeout=10) as r:
            return bool(json.load(r).get("success"))
    except (OSError, ValueError):
        return False


def new_link(email, ip, purpose="login", player_id=None, wait=None, agent=""):
    """Store a link for this address and return (its token, the code the asking device shows).
    wait is the asking device's secret, for sending the sign-in back to it. Raises AuthError when too many were asked for."""
    now = time.time()
    window = config.LINK_MINUTES * 60
    recent = db.one("SELECT COUNT(*) FROM links WHERE email = ? AND created > ?", email, now - window)[0]
    if recent >= config.LINKS_PER_EMAIL:
        raise AuthError(f"A few links have gone to that address already. Try the newest one, or wait "
                        f"{config.LINK_MINUTES} minutes.")
    if ip and db.one("SELECT COUNT(*) FROM links WHERE ip = ? AND created > ?", ip, now - 3600)[0] >= config.LINKS_PER_IP:
        raise AuthError("Too many sign-in emails from here. Try again in an hour.")
    token = secrets.token_urlsafe(32)
    code = f"{secrets.randbelow(10000):04d}" if wait else ""
    db.connect().execute(
        "INSERT INTO links (token_hash, email, ip, purpose, player_id, created, expires, wait_hash, code, agent) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (_hash(token), email, ip or "", purpose, player_id, now, now + window, _hash(wait) if wait else None, code,
         (agent or "")[:200]))
    db.connect().execute("DELETE FROM links WHERE created < ?", (now - 7 * 86400,))
    return token, code


def new_wait(old=""):
    """The asking device's secret: the one it has already (so its earlier links still reach it), or a new one."""
    return old if re.fullmatch(r"[A-Za-z0-9_-]{32,64}", old or "") else secrets.token_urlsafe(32)


def device_name(agent):
    """ "Firefox on Android" from a user-agent string, so the link's page can say which device asked."""
    agent = agent or ""
    browser = next((name for key, name in (("Edg/", "Edge"), ("OPR/", "Opera"), ("SamsungBrowser", "Samsung Internet"),
                                           ("Firefox", "Firefox"), ("FxiOS", "Firefox"), ("CriOS", "Chrome"),
                                           ("Chrome", "Chrome"), ("Safari", "Safari")) if key in agent), "a browser")
    system = next((name for key, name in (("Android", "Android"), ("iPhone", "iPhone"), ("iPad", "iPad"),
                                          ("Windows", "Windows"), ("Mac OS", "Mac"), ("CrOS", "ChromeOS"),
                                          ("Linux", "Linux")) if key in agent), "")
    return f"{browser} on {system}" if system else browser


def link_url(token):
    return f"{config.BASE_URL}/auth/verify?token={urllib.parse.quote(token)}"


def peek_link(token, wait=""):
    """What a link is for, without using it up (email apps open links to preview them). When it was asked for
    somewhere else, which device that was and the code it shows, so the link can sign that one in instead."""
    row = db.one("SELECT * FROM links WHERE token_hash = ?", _hash(token or ""))
    if row is None or row["used"] or row["expires"] < time.time():
        return None
    info = {"email": row["email"], "purpose": row["purpose"]}
    if row["wait_hash"] and row["wait_hash"] != _hash(wait or ""):
        info["asked_on"] = {"device": device_name(row["agent"]), "code": row["code"]}
    if row["purpose"] == "claim":
        guest = db.one("SELECT p.name, u.name AS host FROM players p JOIN users u ON u.id = p.owner_id WHERE p.id = ?",
                       row["player_id"])
        if guest is not None:
            info.update(guest=guest["name"], host=guest["host"])
    return info


def use_link(token, agent="", for_asker=False):
    """Spend the link: find or make the account, start a session. Returns (user id, session token, claimed guest name).
    With for_asker the session is for the device that asked for the link, not this one: it's marked approved,
    and that device picks it up (collect_wait), so the session token returned is None."""
    now = time.time()
    with db.transaction() as conn:
        row = conn.execute("SELECT * FROM links WHERE token_hash = ?", (_hash(token or ""),)).fetchone()
        if row is None or row["used"] or row["expires"] < now:
            raise AuthError("That link has expired or was already used. Ask for a new one.")
        if for_asker and not row["wait_hash"]:
            raise AuthError("This link can only sign in the device it's opened on.")
        conn.execute("UPDATE links SET used = ?, approved = ? WHERE id = ?", (now, now if for_asker else None, row["id"]))
        user = conn.execute("SELECT * FROM users WHERE email = ?", (row["email"],)).fetchone()
        stamp = datetime.now().isoformat(timespec="seconds")
        if user is None:
            user_id = conn.execute("INSERT INTO users (email, created) VALUES (?, ?)", (row["email"], stamp)).lastrowid
        else:
            user_id = user["id"]
        conn.execute("UPDATE users SET last_login = ? WHERE id = ?", (stamp, user_id))
        own = self_player(conn, user_id)
        claimed = None
        if row["purpose"] == "claim":
            claimed = claim_guest(conn, row["player_id"], user_id, own)
        session = None if for_asker else new_session(conn, user_id, agent)
    return user_id, session, claimed


def new_session(conn, user_id, agent=""):
    session = secrets.token_urlsafe(32)
    now = time.time()
    conn.execute("INSERT INTO sessions (token_hash, user_id, created, last_seen, agent) VALUES (?, ?, ?, ?, ?)",
                 (_hash(session), user_id, now, now, (agent or "")[:200]))
    return session


def collect_wait(wait, agent=""):
    """For the device that asked for a link: ("signed_in", session token) once the link was opened somewhere else
    and sent its sign-in here; ("waiting", None) while a link it asked for is still out; ("expired", None) after."""
    if not wait:
        return "expired", None
    now = time.time()
    with db.transaction() as conn:
        row = conn.execute("SELECT * FROM links WHERE wait_hash = ? AND approved > ? AND collected IS NULL "
                           "ORDER BY approved DESC LIMIT 1", (_hash(wait), now - config.LINK_MINUTES * 60)).fetchone()
        if row is not None:
            user = conn.execute("SELECT id FROM users WHERE email = ?", (row["email"],)).fetchone()
            conn.execute("UPDATE links SET collected = ? WHERE wait_hash = ?", (now, row["wait_hash"]))
            if user is not None:
                return "signed_in", new_session(conn, user["id"], agent)
        out = conn.execute("SELECT 1 FROM links WHERE wait_hash = ? AND used IS NULL AND expires > ?",
                           (_hash(wait), now)).fetchone()
    return ("waiting" if out else "expired"), None


def self_player(conn, user_id):
    """The account holder's own seat at their table, made the first time it's needed."""
    row = conn.execute("SELECT id FROM players WHERE owner_id = ? AND is_self = 1", (user_id,)).fetchone()
    if row is not None:
        return row["id"]
    name = conn.execute("SELECT name FROM users WHERE id = ?", (user_id,)).fetchone()["name"]
    return conn.execute("INSERT INTO players (owner_id, is_self, name, created) VALUES (?, 1, ?, ?)",
                        (user_id, name or "Me", datetime.now().isoformat(timespec="seconds"))).lastrowid


def claim_guest(conn, guest_id, user_id, own_id):
    """Move a guest's throws onto this account's own player, then drop the guest. Returns the guest's name."""
    guest = conn.execute("SELECT * FROM players WHERE id = ? AND is_self = 0", (guest_id,)).fetchone()
    if guest is None or guest["owner_id"] == user_id:
        return None
    conn.execute("UPDATE throws SET player_id = ?, table_before = NULL, locked = 1 WHERE player_id = ?", (own_id, guest_id))
    conn.execute("DELETE FROM players WHERE id = ?", (guest_id,))
    user = conn.execute("SELECT name FROM users WHERE id = ?", (user_id,)).fetchone()
    if not user["name"]:          # a brand new account takes the name they played under
        conn.execute("UPDATE users SET name = ? WHERE id = ?", (guest["name"], user_id))
        conn.execute("UPDATE players SET name = ? WHERE id = ?", (guest["name"], own_id))
    return guest["name"]


def session_user(token):
    """The signed-in user for a session cookie, or None."""
    if not token:
        return None
    row = db.one("SELECT s.id AS sid, s.last_seen, u.* FROM sessions s JOIN users u ON u.id = s.user_id "
                 "WHERE s.token_hash = ?", _hash(token))
    now = time.time()
    if row is None:
        return None
    if row["last_seen"] < now - config.SESSION_DAYS * 86400:
        db.connect().execute("DELETE FROM sessions WHERE id = ?", (row["sid"],))
        return None
    if row["last_seen"] < now - 3600:
        db.connect().execute("UPDATE sessions SET last_seen = ? WHERE id = ?", (now, row["sid"]))
    return row


def end_session(token, everywhere_for=None):
    if everywhere_for is not None:
        db.connect().execute("DELETE FROM sessions WHERE user_id = ?", (everywhere_for,))
    elif token:
        db.connect().execute("DELETE FROM sessions WHERE token_hash = ?", (_hash(token),))
