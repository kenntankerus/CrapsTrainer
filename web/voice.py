"""
The stickman's voice for the browser. For each line it finds the best recording it can, in this order:

  1. a line recorded with make_voice_pack.py (voice_packs/<voice>/<line>.mp3), sent as it is
  2. ElevenLabs, in the voice you designed there (CRAPS_ELEVENLABS_API_KEY and CRAPS_ELEVENLABS_VOICES)
  3. Kokoro, a free voice that runs on this computer (python -m web.get_voices fetches its model)
  4. edge-tts, Microsoft's online voices, as the desktop apps use them

Each line is made once and kept in the data folder. When none of them can make it, the page speaks the line
with the browser's own voice. Personal calls ("Pay the hard eight, forty-five dollars") go through the same
engines but are made as they're needed; see web/calls.py.
"""

import asyncio
import hashlib
import importlib.util
import json
import logging
import os
import re
import threading
import time
import urllib.error
import urllib.request
import wave
from datetime import date

from stickman import MOODS, VOICES, pack_name, strip_tags, voice_lines

from . import config, db

log = logging.getLogger("craps.voice")
SLUGS = {pack_name(v): v for v in VOICES}
CLIP_RE = re.compile(r"^([a-zA-Z0-9_]+)_(\d+)$")
_locks = {}

# Each character in Kokoro: its voice, and how much higher (or lower) to pitch it
KOKORO_VOICES = {"Andrew": ("am_michael", 1.0), "Brian": ("am_fenrir", 1.0), "Ava": ("af_heart", 1.0),
                 "Aria": ("af_bella", 1.0), "Shean (Connery-ish)": ("bm_george", 0.94),
                 "Precious (Gollum-ish)": ("bm_lewis", 1.15)}
KOKORO_FILES = ("kokoro-v1.0.onnx", "voices-v1.0.bin")
DIRECTED = ("eleven_v4", "eleven_v3")                         # ElevenLabs models that act out [stage directions]
MOOD_DIRECTIONS = {"hype": "[excited]", "down": "[sighs]"}    # as make_voice_pack.py adds them


def spoken(text, voice):
    """The words as they are said: stage directions dropped, and bent for the characters."""
    text = strip_tags(text)
    bend = VOICES[voice][3] if voice in VOICES else None
    return bend(text) if bend else text


def find_line(voice, clip):
    """(text, mood) for a clip name like 'co_point_6_1', or None."""
    match = CLIP_RE.match(clip)
    if not match:
        return None
    variants, mood = voice_lines(voice).get(match.group(1), (None, None))
    index = int(match.group(2))
    if variants is None or index >= len(variants):
        return None
    return variants[index], mood


def clip_names(voice):
    return [f"{key}_{i}" for key, (variants, _) in voice_lines(voice).items() for i in range(len(variants))]


# ---------------------------------------------------------------- which engines a voice has
def kokoro_ready():
    if not all(os.path.isfile(os.path.join(config.KOKORO_DIR, f)) for f in KOKORO_FILES):
        return False
    return importlib.util.find_spec("kokoro_onnx") is not None


def engines(voice):
    """The engines that can make this voice's lines, best first."""
    order = []
    if config.ELEVENLABS_KEY and config.ELEVENLABS_VOICES.get(voice):
        order.append("elevenlabs")
    if voice in KOKORO_VOICES and kokoro_ready():
        order.append("kokoro")
    if config.TTS:
        order.append("edge")
    return order


def personal_engine(voice, premium):
    """The engine a personal call is made with, or None. It must be the voice's best one, so the call sounds like
    the rest of what the stickman says; ElevenLabs makes them only for the premium accounts."""
    order = engines(voice)
    if not order or (order[0] == "elevenlabs" and not premium):
        return None
    return order[0]


# ---------------------------------------------------------------- the engines
def _cache(key, suffix):
    return os.path.join(config.VOICE_CACHE, hashlib.md5(key.encode()).hexdigest() + suffix)


def edge_args(text, mood, voice):
    """The cache file and edge-tts settings for a line, worked out as dice_tracker_v2.Speaker does, so the
    desktop's voice_cache folder can be copied into the data folder as it is."""
    name, rate_shift, pitch_shift, bend = VOICES[voice]
    rate, pitch, volume = MOODS[mood]
    rate, pitch, volume = f"{rate + rate_shift:+d}%", f"{pitch + pitch_shift:+d}Hz", f"{volume:+d}%"
    text = strip_tags(text)
    if bend:
        text = bend(text)
    return _cache(f"{name}{rate}{pitch}{volume}{text}", ".mp3"), (text, name, rate, pitch, volume)



async def _edge(text, mood, voice):
    path, (words, name, rate, pitch, volume) = edge_args(text, mood, voice)
    if os.path.isfile(path):
        return path
    import edge_tts
    part = path + ".part"
    await asyncio.wait_for(edge_tts.Communicate(words, name, rate=rate, pitch=pitch, volume=volume).save(part), 20)
    os.replace(part, path)
    return path


_kokoro = None
_kokoro_lock = threading.Lock()


def kokoro_args(text, mood, voice):
    kvoice, pitch = KOKORO_VOICES[voice]
    speed = round((1 + (MOODS[mood][0] + VOICES[voice][1]) / 100) / pitch, 3)
    words = spoken(text, voice)
    return _cache(f"kokoro|{kvoice}|{speed}|{pitch}|{words}", ".wav"), (words, kvoice, speed, pitch)


def _kokoro_make(path, words, kvoice, speed, pitch):
    global _kokoro
    import numpy as np
    with _kokoro_lock:
        if _kokoro is None:
            from kokoro_onnx import Kokoro
            _kokoro = Kokoro(*(os.path.join(config.KOKORO_DIR, f) for f in KOKORO_FILES))
        samples, rate = _kokoro.create(words, voice=kvoice, speed=speed, lang="en-gb" if kvoice[0] == "b" else "en-us")
    samples = np.asarray(samples, dtype=np.float32)
    if pitch != 1:            # play it faster to raise the voice (the speed above already allows for it)
        samples = np.interp(np.arange(0, len(samples) - 1, pitch), np.arange(len(samples)), samples)
    pcm = (np.clip(samples, -1, 1) * 32767).astype("<i2").tobytes()
    part = path + ".part"
    with wave.open(part, "wb") as f:
        f.setnchannels(1)
        f.setsampwidth(2)
        f.setframerate(rate)
        f.writeframes(pcm)
    os.replace(part, path)


async def _kokoro_engine(text, mood, voice):
    path, args = kokoro_args(text, mood, voice)
    if not os.path.isfile(path):
        await asyncio.to_thread(_kokoro_make, path, *args)
    return path


def eleven_text(text, mood, model):
    """The text as sent: stage directions kept (plus one for the mood) on models that act them out."""
    if model not in DIRECTED:
        return strip_tags(text)
    if text.startswith("[") or mood not in MOOD_DIRECTIONS:
        return text
    return f"{MOOD_DIRECTIONS[mood]} {text}"


def eleven_args(text, mood, voice, fast=False):
    model = config.ELEVENLABS_FAST_MODEL if fast else config.ELEVENLABS_MODEL
    voice_id = config.ELEVENLABS_VOICES[voice]
    words = eleven_text(text, mood, model)
    return _cache(f"eleven|{voice_id}|{model}|{words}", ".mp3"), (words, voice_id, model)


def spend(chars):
    """Count characters sent to ElevenLabs today. False (and nothing counted) if that would pass the daily cap."""
    day = date.today().isoformat()
    with db.transaction() as conn:
        row = conn.execute("SELECT chars FROM tts_usage WHERE day = ?", (day,)).fetchone()
        used = row["chars"] if row else 0
        if config.ELEVENLABS_DAILY_CHARS and used + chars > config.ELEVENLABS_DAILY_CHARS:
            return False
        conn.execute("INSERT INTO tts_usage (day, chars) VALUES (?, ?) ON CONFLICT(day) DO UPDATE SET chars = ?",
                     (day, used + chars, used + chars))
    return True


def _eleven_make(path, words, voice_id, model):
    if not spend(len(words)):
        raise RuntimeError("ElevenLabs daily character limit reached")
    body = json.dumps({"text": words, "model_id": model,
                       "voice_settings": {"stability": 0.5, "similarity_boost": 0.75}}).encode()
    request = urllib.request.Request(
        f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}?output_format=mp3_44100_128", data=body,
        headers={"xi-api-key": config.ELEVENLABS_KEY, "Content-Type": "application/json", "Accept": "audio/mpeg"})
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            audio = response.read()
    except urllib.error.HTTPError as error:
        spend(-len(words))
        raise RuntimeError(f"ElevenLabs said {error.code}: {error.read()[:300]!r}")
    part = path + ".part"
    with open(part, "wb") as f:
        f.write(audio)
    os.replace(part, path)


async def _eleven(text, mood, voice, fast=False):
    path, args = eleven_args(text, mood, voice, fast)
    if not os.path.isfile(path):
        await asyncio.to_thread(_eleven_make, path, *args)
    return path


async def make(engine, text, mood, voice, fast=False):
    """The file for a line from one engine, made if need be. Raises when the engine can't."""
    os.makedirs(config.VOICE_CACHE, exist_ok=True)
    path = {"edge": lambda: edge_args(text, mood, voice)[0], "kokoro": lambda: kokoro_args(text, mood, voice)[0],
            "elevenlabs": lambda: eleven_args(text, mood, voice, fast)[0]}[engine]()
    if os.path.isfile(path):
        return path
    lock = _locks.setdefault(path, asyncio.Lock())
    async with lock:
        try:
            if engine == "edge":
                return await _edge(text, mood, voice)
            if engine == "kokoro":
                return await _kokoro_engine(text, mood, voice)
            return await _eleven(text, mood, voice, fast)
        finally:
            _locks.pop(path, None)


async def clip_path(slug, clip):
    """The recording of one of a voice's fixed lines, made if need be. None when there isn't one and it can't be made."""
    voice = SLUGS.get(slug)
    line = find_line(voice, clip) if voice else None
    if line is None:
        return None
    recorded = os.path.join(config.VOICE_PACKS, slug, clip + ".mp3")
    if os.path.isfile(recorded):
        return recorded
    for engine in engines(voice):
        try:
            return await make(engine, *line, voice)
        except Exception as error:
            log.info("%s couldn't make %s/%s: %s", engine, slug, clip, error)
    return None


# ---------------------------------------------------------------- personal calls
def remember_line(voice, text, mood, engine):
    """Store a personal call and return the token its recording is fetched by."""
    token = hashlib.sha1(f"{engine}|{voice}|{mood}|{text}".encode()).hexdigest()[:24]
    db.connect().execute("INSERT OR IGNORE INTO lines (token, voice, text, mood, engine, created) VALUES (?, ?, ?, ?, ?, ?)",
                         (token, voice, text, mood, engine, time.time()))
    return token


async def line_path(slug, token):
    row = db.one("SELECT * FROM lines WHERE token = ?", token)
    if row is None or SLUGS.get(slug) != row["voice"]:
        return None
    try:
        return await make(row["engine"], row["text"], row["mood"], row["voice"], fast=True)
    except Exception as error:
        log.info("%s couldn't make a personal call: %s", row["engine"], error)
        return None


def media_type(path):
    return "audio/wav" if path.endswith(".wav") else "audio/mpeg"


async def prepare(voice):
    """Make every fixed line of a voice that isn't made yet (python -m web.get_voices --prepare runs this)."""
    made = 0
    for clip in clip_names(voice):
        if await clip_path(pack_name(voice), clip):
            made += 1
    return made
