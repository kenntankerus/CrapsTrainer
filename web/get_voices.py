"""
The free voice that runs on this computer (Kokoro), and recording the stickman's lines ahead of time.

    python -m web.get_voices                 fetch Kokoro's model files (about 340 MB) into data/models/kokoro
    python -m web.get_voices --prepare       record every voice's fixed lines that aren't recorded yet
    python -m web.get_voices --prepare Ava   just that voice ("Precious (Gollum-ish)" needs the quotes)

Recording ahead of time is optional: a line is otherwise made the first time it's needed. Each voice uses
the best engine it has (see web/voice.py), so with Kokoro in place, --prepare can run overnight on a slow laptop.
"""

import argparse
import asyncio
import os
import sys
import urllib.request

from . import config, db

sys.path.insert(0, config.ROOT)

RELEASE = "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/"


def fetch(name):
    target = os.path.join(config.KOKORO_DIR, name)
    if os.path.isfile(target):
        print(f"{name}: already here")
        return
    os.makedirs(config.KOKORO_DIR, exist_ok=True)
    part = target + ".part"
    with urllib.request.urlopen(RELEASE + name, timeout=60) as response, open(part, "wb") as out:
        total = int(response.headers.get("Content-Length") or 0)
        done = 0
        while chunk := response.read(1 << 20):
            out.write(chunk)
            done += len(chunk)
            if total:
                print(f"\r{name}: {done * 100 // total}% of {total // (1 << 20)} MB", end="", flush=True)
    os.replace(part, target)
    print(f"\r{name}: done{' ' * 30}")


def main(argv=None):
    from stickman import VOICES
    from . import voice

    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--prepare", nargs="*", metavar="VOICE", help="record the fixed lines (all voices if none named)")
    args = parser.parse_args(argv)
    if args.prepare is None:
        for name in voice.KOKORO_FILES:
            fetch(name)
        print("Kokoro is ready." if voice.kokoro_ready() else "The files are here, but the kokoro-onnx package isn't "
              "installed: pip install -r requirements-web.txt")
        return
    db.init()
    names = args.prepare or list(VOICES)
    for name in names:
        if name not in VOICES:
            sys.exit(f"No voice called {name!r}. The voices are: {', '.join(VOICES)}")
        engines = voice.engines(name)
        print(f"{name}: {', '.join(engines) or 'no engine available'}...", flush=True)
        made = asyncio.run(voice.prepare(name))
        print(f"{name}: {made} of {len(voice.clip_names(name))} lines recorded")


if __name__ == "__main__":
    main()
