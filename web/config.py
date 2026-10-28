"""
Settings, read from the environment (or a .env file next to the repo's files).
Every name starts with CRAPS_; .env.example lists them all.
"""

import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _load_dotenv(path):
    """KEY=value lines; values already in the environment win."""
    try:
        with open(path) as f:
            lines = f.readlines()
    except OSError:
        return
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
            value = value[1:-1]
        os.environ.setdefault(key.strip(), value)


_load_dotenv(os.path.join(ROOT, ".env"))


def _get(name, default=""):
    return os.environ.get(f"CRAPS_{name}", default).strip()


def _flag(name, default=False):
    value = _get(name, "1" if default else "0").lower()
    return value in ("1", "true", "yes", "on")


DATA_DIR = os.path.abspath(_get("DATA_DIR", os.path.join(ROOT, "data")))
DB_PATH = os.path.join(DATA_DIR, "craps.db")
CROP_DIR = os.path.join(DATA_DIR, "dice_crops")
VOICE_CACHE = os.path.join(DATA_DIR, "voice_cache")
VOICE_PACKS = os.path.abspath(_get("VOICE_PACKS", os.path.join(ROOT, "voice_packs")))
BASE_URL = _get("BASE_URL", f"http://localhost:{_get('PORT', '8000')}").rstrip("/")
SECURE_COOKIES = BASE_URL.startswith("https://")
SITE_NAME = _get("SITE_NAME", "Craps Trainer")

# Email for the sign-in links. With no SMTP host the links are printed in the server log instead,
# which is only good for trying the app out on your own computer.
SMTP_HOST = _get("SMTP_HOST")
SMTP_PORT = int(_get("SMTP_PORT", "587") or 587)
SMTP_USER = _get("SMTP_USER")
SMTP_PASSWORD = _get("SMTP_PASSWORD")
SMTP_SECURITY = _get("SMTP_SECURITY", "starttls").lower()      # starttls, ssl or none
MAIL_FROM = _get("MAIL_FROM", f"{SITE_NAME} <login@localhost>")

# Cloudflare Turnstile, the bot check on the sign-in form. Off when no keys are set.
TURNSTILE_SITE_KEY = _get("TURNSTILE_SITE_KEY")
TURNSTILE_SECRET = _get("TURNSTILE_SECRET")

# Behind a Cloudflare Tunnel every request arrives from the tunnel, so the visitor's own address
# comes from the CF-Connecting-IP header. Only turn this on behind a proxy that sets it.
TRUST_PROXY = _flag("TRUST_PROXY")

# The stickman's voice: lines are made with edge-tts (needs internet) and kept in VOICE_CACHE.
TTS = _flag("TTS", True)

LINK_MINUTES = 15                  # a sign-in link works once, for this long
SESSION_DAYS = 90                  # how long a device stays signed in without being used
LINKS_PER_EMAIL = 3                # sign-in emails per address per LINK_MINUTES
LINKS_PER_IP = 10                  # sign-in emails per visitor address per hour
MAX_GUESTS = 20
