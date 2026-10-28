"""
The database: one SQLite file in the data folder. Accounts, sign-in links and sessions,
the players at each account's table (the account holder plus any guests), and every throw.
"""

import json
import os
import sqlite3
import threading
from contextlib import contextmanager

from . import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY,
    email TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL DEFAULT '',
    created TEXT NOT NULL,
    last_login TEXT,
    settings TEXT NOT NULL DEFAULT '{}'
);
-- one row per emailed link; only a hash of the token is kept
CREATE TABLE IF NOT EXISTS links (
    id INTEGER PRIMARY KEY,
    token_hash TEXT NOT NULL UNIQUE,
    email TEXT NOT NULL,
    ip TEXT NOT NULL DEFAULT '',
    purpose TEXT NOT NULL DEFAULT 'login',      -- 'login', or 'claim' for a guest taking their history
    player_id INTEGER,                          -- the guest, for a claim
    created REAL NOT NULL,
    expires REAL NOT NULL,
    used REAL
);
CREATE INDEX IF NOT EXISTS links_email ON links(email, created);
CREATE INDEX IF NOT EXISTS links_ip ON links(ip, created);
CREATE TABLE IF NOT EXISTS sessions (
    id INTEGER PRIMARY KEY,
    token_hash TEXT NOT NULL UNIQUE,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    created REAL NOT NULL,
    last_seen REAL NOT NULL,
    agent TEXT NOT NULL DEFAULT ''
);
-- everyone who plays at an account's table: the account holder (is_self = 1) and their guests
CREATE TABLE IF NOT EXISTS players (
    id INTEGER PRIMARY KEY,
    owner_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    is_self INTEGER NOT NULL DEFAULT 0,
    name TEXT NOT NULL,
    table_state TEXT,                           -- CrapsTable.to_dict() as JSON
    last_roll TEXT,                             -- what the "last roll" panel shows, as JSON
    created TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS players_owner ON players(owner_id);
-- the same columns as dice_throws.csv, plus what the table needs to take a roll back
CREATE TABLE IF NOT EXISTS throws (
    id INTEGER PRIMARY KEY,
    player_id INTEGER NOT NULL REFERENCES players(id) ON DELETE CASCADE,
    throw_id TEXT NOT NULL,
    timestamp TEXT NOT NULL,
    session TEXT NOT NULL,
    dice_set TEXT NOT NULL,
    position TEXT NOT NULL,
    die1 INTEGER NOT NULL,
    die2 INTEGER NOT NULL,
    total INTEGER NOT NULL,
    back_wall INTEGER NOT NULL,
    source TEXT NOT NULL,
    camera_read TEXT NOT NULL DEFAULT '',
    table_before TEXT,                          -- the table just before a craps-game roll; NULL for tracker throws
    net INTEGER,                                -- what the roll won or lost, in cents
    note TEXT NOT NULL DEFAULT '',              -- "point is 6", "seven out after 9 rolls", ...
    locked INTEGER NOT NULL DEFAULT 0           -- imported or claimed: kept, but Undo and Fix last pass it by
);
CREATE INDEX IF NOT EXISTS throws_player ON throws(player_id, id);
-- the stickman's personal calls ("Pay the hard eight, forty-five dollars"), made when first played
CREATE TABLE IF NOT EXISTS lines (
    token TEXT PRIMARY KEY,
    voice TEXT NOT NULL,
    text TEXT NOT NULL,
    mood TEXT NOT NULL,
    engine TEXT NOT NULL,
    created REAL NOT NULL
);
-- characters sent to ElevenLabs each day, for CRAPS_ELEVENLABS_DAILY_CHARS
CREATE TABLE IF NOT EXISTS tts_usage (
    day TEXT PRIMARY KEY,
    chars INTEGER NOT NULL
);
"""

_local = threading.local()


def connect():
    """This thread's connection (FastAPI runs plain functions on a pool of threads)."""
    conn = getattr(_local, "conn", None)
    if conn is None or getattr(_local, "path", None) != config.DB_PATH:
        os.makedirs(os.path.dirname(config.DB_PATH), exist_ok=True)
        conn = sqlite3.connect(config.DB_PATH, timeout=15, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA busy_timeout = 15000")
        _local.conn, _local.path = conn, config.DB_PATH
    return conn


def init():
    connect().executescript(SCHEMA)


@contextmanager
def transaction():
    """BEGIN IMMEDIATE ... COMMIT, so two devices changing the same table take turns."""
    conn = connect()
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield conn
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    else:
        conn.execute("COMMIT")


def one(sql, *args):
    return connect().execute(sql, args).fetchone()


def all_rows(sql, *args):
    return connect().execute(sql, args).fetchall()


def settings_of(user):
    try:
        value = json.loads(user["settings"] or "{}")
        return value if isinstance(value, dict) else {}
    except ValueError:
        return {}
