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

# The webcam at the table: a number (0 is the first camera), a device such as /dev/video0, or "demo" for a
# pretend camera that throws drawn dice. Empty means no camera. Only the accounts listed may use it.
CAMERA_SOURCE = _get("CAMERA")
CAMERA_USERS = {e.strip().lower() for e in _get("CAMERA_USERS").split(",") if e.strip()}
PREMIUM_USERS = {e.strip().lower() for e in _get("PREMIUM_USERS").split(",") if e.strip()} or CAMERA_USERS

# Voices. ElevenLabs (the same key make_voice_pack.py uses) records the lines in your designed voices: list each
# character's voice ID as  Name=ID; Name=ID  using the names in the Stickman list. Personal calls (names, payouts)
# are made as they're needed, so only the accounts in CRAPS_PREMIUM_USERS get them from ElevenLabs, and no more
# than CRAPS_ELEVENLABS_DAILY_CHARS characters a day go to it in all.
ELEVENLABS_KEY = _get("ELEVENLABS_API_KEY") or os.environ.get("ELEVENLABS_API_KEY", "").strip()
ELEVENLABS_VOICES = dict(
    (name.strip(), voice_id.strip()) for name, _, voice_id in
    (pair.partition("=") for pair in _get("ELEVENLABS_VOICES").split(";")) if name.strip() and voice_id.strip())
ELEVENLABS_MODEL = _get("ELEVENLABS_MODEL", "eleven_v3")                 # the stickman's fixed lines
ELEVENLABS_FAST_MODEL = _get("ELEVENLABS_FAST_MODEL", "eleven_flash_v2_5")   # personal calls, made while you wait
ELEVENLABS_DAILY_CHARS = int(_get("ELEVENLABS_DAILY_CHARS", "20000") or 0)
# Kokoro, a free voice that runs on this computer, once its model files are in this folder (python -m web.get_voices)
KOKORO_DIR = os.path.abspath(_get("KOKORO_DIR", os.path.join(DATA_DIR, "models", "kokoro")))
SOUND_DIR = os.path.join(DATA_DIR, "sounds")     # recorded room and crowd sounds (python -m web.make_sounds)

LINK_MINUTES = 15                  # a sign-in link works once, for this long
SESSION_DAYS = 90                  # how long a device stays signed in without being used
LINKS_PER_EMAIL = 3                # sign-in emails per address per LINK_MINUTES
LINKS_PER_IP = 10                  # sign-in emails per visitor address per hour
MAX_GUESTS = 20
