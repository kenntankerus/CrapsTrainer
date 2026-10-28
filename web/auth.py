"""
Signing in with an emailed link: no passwords. Asking for a link makes an account the first
time an address is used. The link signs one device in for SESSION_DAYS since it was last used.
Only hashes of the link and session tokens are stored, so a copy of the database can't sign anyone in.
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


def new_link(email, ip, purpose="login", player_id=None):
    """Store a link for this address and return its token. Raises AuthError when too many were asked for."""
    now = time.time()
    window = config.LINK_MINUTES * 60
    recent = db.one("SELECT COUNT(*) FROM links WHERE email = ? AND created > ?", email, now - window)[0]
    if recent >= config.LINKS_PER_EMAIL:
        raise AuthError(f"A few links have gone to that address already. Try the newest one, or wait "
                        f"{config.LINK_MINUTES} minutes.")
    if ip and db.one("SELECT COUNT(*) FROM links WHERE ip = ? AND created > ?", ip, now - 3600)[0] >= config.LINKS_PER_IP:
        raise AuthError("Too many sign-in emails from here. Try again in an hour.")
    token = secrets.token_urlsafe(32)
    db.connect().execute(
        "INSERT INTO links (token_hash, email, ip, purpose, player_id, created, expires) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (_hash(token), email, ip or "", purpose, player_id, now, now + window))
    db.connect().execute("DELETE FROM links WHERE created < ?", (now - 7 * 86400,))
    return token


def link_url(token):
    return f"{config.BASE_URL}/auth/verify?token={urllib.parse.quote(token)}"


def peek_link(token):
    """What a link is for, without using it up (email apps open links to preview them)."""
    row = db.one("SELECT * FROM links WHERE token_hash = ?", _hash(token or ""))
    if row is None or row["used"] or row["expires"] < time.time():
        return None
    info = {"email": row["email"], "purpose": row["purpose"]}
    if row["purpose"] == "claim":
        guest = db.one("SELECT p.name, u.name AS host FROM players p JOIN users u ON u.id = p.owner_id WHERE p.id = ?",
                       row["player_id"])
        if guest is not None:
            info.update(guest=guest["name"], host=guest["host"])
    return info


def use_link(token, agent=""):
    """Spend the link: find or make the account, start a session. Returns (user id, session token, claimed guest name)."""
    now = time.time()
    with db.transaction() as conn:
        row = conn.execute("SELECT * FROM links WHERE token_hash = ?", (_hash(token or ""),)).fetchone()
        if row is None or row["used"] or row["expires"] < now:
            raise AuthError("That link has expired or was already used. Ask for a new one.")
        conn.execute("UPDATE links SET used = ? WHERE id = ?", (now, row["id"]))
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
        session = secrets.token_urlsafe(32)
        conn.execute("INSERT INTO sessions (token_hash, user_id, created, last_seen, agent) VALUES (?, ?, ?, ?, ?)",
                     (_hash(session), user_id, now, now, (agent or "")[:200]))
    return user_id, session, claimed


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
