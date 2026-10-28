"""
The stickman's voice for the browser. A line recorded with make_voice_pack.py (voice_packs/<voice>/<line>.mp3)
is sent as it is; any other line is made once with edge-tts, the same way the desktop apps make them, and kept in
the data folder. Without edge-tts or the internet the browser speaks the line with its own voice instead.
"""

import asyncio
import hashlib
import os
import re

from stickman import MOODS, VOICES, pack_name, strip_tags, voice_lines

from . import config

SLUGS = {pack_name(v): v for v in VOICES}
CLIP_RE = re.compile(r"^([a-zA-Z0-9_]+)_(\d+)$")
_locks = {}


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


def tts_args(text, mood, voice):
    """The cache file and edge-tts settings for a line, worked out as dice_tracker_v2.Speaker does, so the
    desktop's voice_cache folder can be copied into the data folder as it is."""
    name, rate_shift, pitch_shift, bend = VOICES[voice]
    rate, pitch, volume = MOODS[mood]
    rate, pitch, volume = f"{rate + rate_shift:+d}%", f"{pitch + pitch_shift:+d}Hz", f"{volume:+d}%"
    text = strip_tags(text)
    if bend:
        text = bend(text)
    key = f"{name}{rate}{pitch}{volume}{text}"
    return os.path.join(config.VOICE_CACHE, hashlib.md5(key.encode()).hexdigest() + ".mp3"), (text, name, rate, pitch, volume)


async def clip_path(slug, clip):
    """The mp3 for a voice's line, made if need be. None when there isn't one and it can't be made."""
    voice = SLUGS.get(slug)
    line = find_line(voice, clip) if voice else None
    if line is None:
        return None
    recorded = os.path.join(config.VOICE_PACKS, slug, clip + ".mp3")
    if os.path.isfile(recorded):
        return recorded
    path, args = tts_args(*line, voice)
    if os.path.isfile(path):
        return path
    if not config.TTS:
        return None
    lock = _locks.setdefault(path, asyncio.Lock())
    async with lock:
        if os.path.isfile(path):
            return path
        try:
            import edge_tts
            text, name, rate, pitch, volume = args
            os.makedirs(config.VOICE_CACHE, exist_ok=True)
            part = path + ".part"
            await asyncio.wait_for(
                edge_tts.Communicate(text, name, rate=rate, pitch=pitch, volume=volume).save(part), timeout=20)
            os.replace(part, path)
        except Exception:
            return None
        finally:
            _locks.pop(path, None)
    return path
