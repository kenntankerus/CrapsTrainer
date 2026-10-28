"""
Records the room and crowd sounds with ElevenLabs' sound effects, once:   python -m web.make_sounds
(--redo records them again). Uses CRAPS_ELEVENLABS_API_KEY and keeps them in data/sounds.

Without these files the page makes its own room and crowd sounds; with them, it plays these instead.
"""

import argparse
import json
import os
import sys
import urllib.error
import urllib.request

from . import config

# name: (what to make, seconds)
SOUNDS = {
    "room": ("Ambient room tone of a busy casino around a craps table: distant crowd murmur and chatter, faint "
             "clay chips clinking, far-off slot machine chimes. No music, no voices close by, steady throughout.", 22),
    "cheer": ("A small crowd at a craps table cheering 'yeah!' and clapping for a few seconds, happy.", 3),
    "big_cheer": ("A crowd at a casino craps table erupting in loud cheers, whoops and applause after a huge win.", 5),
    "groan": ("A small crowd at a craps table groaning in disappointment, 'ohhh', as the dice seven out.", 3),
    "aww": ("A few people at a table saying 'aww' softly in mild disappointment.", 2),
    "dice": ("Two dice thrown across a felt craps table, hitting the padded back wall and tumbling to a stop.", 2),
}


def make(name, text, seconds):
    body = json.dumps({"text": text, "duration_seconds": seconds, "prompt_influence": 0.5}).encode()
    request = urllib.request.Request("https://api.elevenlabs.io/v1/sound-generation", data=body,
                                     headers={"xi-api-key": config.ELEVENLABS_KEY, "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=120) as response:
            audio = response.read()
    except urllib.error.HTTPError as error:
        sys.exit(f"ElevenLabs said no ({error.code}): {error.read().decode(errors='replace')}")
    except urllib.error.URLError as error:
        sys.exit(f"Couldn't reach ElevenLabs: {error.reason}")
    os.makedirs(config.SOUND_DIR, exist_ok=True)
    with open(os.path.join(config.SOUND_DIR, name + ".mp3"), "wb") as f:
        f.write(audio)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--redo", action="store_true", help="record them all again")
    args = parser.parse_args(argv)
    if not config.ELEVENLABS_KEY:
        sys.exit("Put your ElevenLabs key in .env as CRAPS_ELEVENLABS_API_KEY first.")
    for name, (text, seconds) in SOUNDS.items():
        if os.path.exists(os.path.join(config.SOUND_DIR, name + ".mp3")) and not args.redo:
            print(f"{name}: already made")
            continue
        make(name, text, seconds)
        print(f"{name}: made")
    print("Reload the page to hear them.")


if __name__ == "__main__":
    main()
